/**
 * Typed exception hierarchy for the struct-x Node SDK.
 *
 * Mirrors the Python SDK's `_exceptions.py` 1:1 so customers running
 * polyglot stacks can port retry / catch logic between languages with
 * no semantic surprises. Every backend error response carries
 * `{"error": str, "code": str}` (see backend/routers/*.py — convention);
 * the SDK maps `code` (or, if absent, HTTP status) to a typed
 * exception class so callers can `catch (e instanceof RateLimitError)`
 * instead of inspecting status codes.
 *
 * Hierarchy is intentionally narrow — three categories that map to
 * three call-site decisions:
 *
 *     StructXError              base; matches anything from the SDK
 *       ApiError                server returned an error response
 *         AuthenticationError   401 — fix your API key
 *         PermissionDeniedError 403 — your key works but lacks scope
 *         RateLimitError        429 — back off; carries credits info
 *         ValidationError       400/422 — fix your input
 *         NotFoundError         404 — wrong slug or id
 *         ServerError           5xx — retry or contact support
 *       TransportError          network/timeout, never reached the server
 */

export class StructXError extends Error {
  constructor(message: string) {
    super(message);
    // Preserve the constructor's name in stack traces. Required for
    // `instanceof` to work across realms (workers, vm-isolates) and
    // for serialized error logs to identify the type.
    this.name = this.constructor.name;
    // Node/V8 — strip the constructor frame from the stack so
    // .stack starts at the call site, not inside this base class.
    if (typeof (Error as { captureStackTrace?: unknown }).captureStackTrace === 'function') {
      (Error as unknown as {
        captureStackTrace(target: object, ctor: unknown): void;
      }).captureStackTrace(this, this.constructor);
    }
  }
}

/**
 * Network-level failure — the request never reached the API or the
 * response never made it back. Always safe to retry idempotent calls;
 * write calls (extract/infer) are *probably* safe but the backend
 * may have partially processed before the connection dropped.
 */
export class TransportError extends StructXError {}

// Keys whose VALUES are stripped from response_body before the body
// is attached to the exception. Customer code that logs the error
// (e.g. via Sentry capture or console.error(JSON.stringify(err)))
// won't leak: API keys, session credentials, or raw payload content.
// Match is case-insensitive against the body's own keys.
const REDACTED_KEYS = new Set<string>([
  'x-api-key',
  'authorization',
  'apikey',
  'api_key',
  'password',
  'token',
  'secret',
  'private_key',
  'content',
]);

/** Sentinel value substituted for any redacted field. Preserves
 *  original size in characters so debugging can see "this field had
 *  data" without seeing what. */
function redactValue(value: unknown): string {
  if (typeof value === 'string') {
    return `<redacted ${value.length} chars>`;
  }
  if (value instanceof Uint8Array) {
    return `<redacted ${value.length} bytes>`;
  }
  return '<redacted>';
}

/** Shallow-copy `body`, replacing any sensitive-key value with the
 *  redacted sentinel. Same posture as the Python SDK's _redact_body. */
function redactBody(body: Record<string, unknown> | null | undefined):
  Record<string, unknown> | null | undefined {
  if (!body) return body;
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(body)) {
    if (REDACTED_KEYS.has(k.toLowerCase())) {
      out[k] = redactValue(body[k]);
    } else {
      out[k] = body[k];
    }
  }
  return out;
}

export interface ApiErrorInit {
  statusCode: number;
  code?: string | null;
  responseBody?: Record<string, unknown> | null;
  requestId?: string | null;
}

/**
 * Server responded with an error. Subclasses below are status/code-
 * typed; if none matches, raw ApiError is raised so callers can still
 * pattern-match `catch (e instanceof ApiError)`.
 *
 * **Privacy posture**: `responseBody` has sensitive keys redacted at
 * exception-construction time — see REDACTED_KEYS. Customer code that
 * logs the error or routes it to Sentry sees `<redacted N chars>`
 * instead of credentials / payload content. `.toString()` ALSO omits
 * the body entirely as a second layer.
 */
export class ApiError extends StructXError {
  public readonly statusCode: number;
  public readonly code: string | null;
  public readonly responseBody: Record<string, unknown> | null;
  public readonly requestId: string | null;

  constructor(message: string, init: ApiErrorInit) {
    super(message);
    this.statusCode = init.statusCode;
    this.code = init.code ?? null;
    // Redact at construction time so any post-creation access
    // (including third-party error formatters) sees the safe version.
    const redacted = redactBody(init.responseBody ?? null);
    this.responseBody = redacted ?? null;
    this.requestId = init.requestId ?? null;
  }

  /** Second layer of defense against `String(err)` / `err.toString()`
   *  patterns in customer logs — the body is intentionally omitted. */
  override toString(): string {
    const bits: string[] = [`status=${this.statusCode}`];
    if (this.code !== null) bits.push(`code=${JSON.stringify(this.code)}`);
    if (this.requestId !== null) bits.push(`request_id=${JSON.stringify(this.requestId)}`);
    return `${this.name}: ${this.message} (${bits.join(', ')})`;
  }
}

/** 401 — the API key is missing, malformed, or revoked. */
export class AuthenticationError extends ApiError {}

/** 403 — the API key is valid but doesn't have access to this
 *  resource (e.g. admin-only endpoint). */
export class PermissionDeniedError extends ApiError {}

/** 404 — template slug, key id, or other resource doesn't exist. */
export class NotFoundError extends ApiError {}

/** 400 / 422 — your request payload is malformed. Common cases:
 *  schema isn't a JSON object, content is too large, template_slug
 *  doesn't resolve. */
export class ValidationError extends ApiError {}

export interface RateLimitErrorInit extends ApiErrorInit {
  retryAfter?: number | null;
  creditsUsed?: number | null;
  creditsRemaining?: number | null;
}

/**
 * 429 — you've hit the daily credit cap OR the per-window request
 * cap. The `retryAfter` attribute (seconds) is populated from the
 * `Retry-After` response header when present. Credits are populated
 * from the response body when the backend supplied them (it does for
 * credit-exhaustion 429s; not for IP-based throttling).
 */
export class RateLimitError extends ApiError {
  public readonly retryAfter: number | null;
  public readonly creditsUsed: number | null;
  public readonly creditsRemaining: number | null;

  constructor(message: string, init: RateLimitErrorInit) {
    super(message, init);
    this.retryAfter = init.retryAfter ?? null;
    this.creditsUsed = init.creditsUsed ?? null;
    this.creditsRemaining = init.creditsRemaining ?? null;
  }
}

/** 5xx — the backend returned a server-side failure. Retrying may
 *  help for 502/503/504; 500 usually means a bug — report it with
 *  the `requestId` attribute attached. */
export class ServerError extends ApiError {}

// ── Status → class map ─────────────────────────────────────

export const ERROR_BY_STATUS = new Map<number, typeof ApiError>([
  [400, ValidationError],
  [401, AuthenticationError],
  [403, PermissionDeniedError],
  [404, NotFoundError],
  [422, ValidationError],
  [429, RateLimitError],
]);
