/**
 * Retry policy — mirrors the Python SDK's `RetryPolicy` dataclass.
 *
 * Defaults are conservative: 3 attempts total, exponential backoff
 * capped at 30s, 5xx-retry enabled for reads but disabled for writes
 * (the client enforces the write-exclusion at call sites; this flag
 * only affects reads). `respectRetryAfter` lets the server tell us
 * when to come back — used by 429s.
 */
export interface RetryPolicyOptions {
  maxAttempts?: number;
  initialBackoff?: number;
  maxBackoff?: number;
  retryOn5xx?: boolean;
  respectRetryAfter?: boolean;
  jitter?: number;
}

export class RetryPolicy {
  public readonly maxAttempts: number;
  public readonly initialBackoff: number;
  public readonly maxBackoff: number;
  public readonly retryOn5xx: boolean;
  public readonly respectRetryAfter: boolean;
  public readonly jitter: number;

  constructor(opts: RetryPolicyOptions = {}) {
    this.maxAttempts = opts.maxAttempts ?? 3;
    this.initialBackoff = opts.initialBackoff ?? 1.0;
    this.maxBackoff = opts.maxBackoff ?? 30.0;
    this.retryOn5xx = opts.retryOn5xx ?? true;
    this.respectRetryAfter = opts.respectRetryAfter ?? true;
    this.jitter = opts.jitter ?? 0.2;
  }

  /**
   * Seconds to sleep before `attempt` (1-indexed). If the server sent
   * `Retry-After`, use that — caps still apply. Otherwise exponential
   * backoff with bounded jitter so simultaneous clients don't
   * thunder-herd on retry.
   */
  backoffFor(attempt: number, retryAfter: number | null = null): number {
    if (retryAfter !== null && this.respectRetryAfter) {
      return Math.min(retryAfter, this.maxBackoff);
    }
    const base = this.initialBackoff * Math.pow(2, attempt - 1);
    const jitterRange = base * this.jitter;
    const offset = (Math.random() * 2 - 1) * jitterRange;
    return Math.min(base + offset, this.maxBackoff);
  }
}

/**
 * Endpoints that mutate / bill — retry only on TransportError, never
 * on 5xx (the server may have partially processed before failing).
 */
export const WRITE_PATHS = new Set<string>([
  '/v1/extract',
  '/v1/extract/batch',
  '/v1/schemas/infer',
  '/v1/synth/generate',
  '/v1/extract/document',
]);

/** `WRITE_PATHS`, plus `POST /v1/schemas/recommendations/{event_id}/augment`,
 *  which bills like `/v1/schemas/infer` but carries an id in its path. */
function isWritePath(path: string): boolean {
  return WRITE_PATHS.has(path)
    || (path.startsWith('/v1/schemas/recommendations/') && path.endsWith('/augment'));
}

/** Whether a 5xx response on `path` should be retried under `policy`. */
export function isRetryableResponse(
  path: string,
  status: number,
  policy: RetryPolicy,
): boolean {
  if (!policy.retryOn5xx) return false;
  if (isWritePath(path)) return false;
  // 501 Not Implemented is a permanent rejection — not transient.
  return status >= 500 && status < 600 && status !== 501;
}
