import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  AuthenticationError,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  RetryPolicy,
  ServerError,
  StructX,
  StructXError,
  TransportError,
  ValidationError,
} from '../src/index.js';

// The published version. The User-Agent check below compares against it so a
// version.ts that drifts from package.json fails here.
const PACKAGE_VERSION = (
  JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { version: string }
).version;

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

beforeEach(() => {
  // Wipe env vars so we don't accidentally inherit a real key during a
  // local `npm test` in a dev shell that has them set.
  delete process.env['STRUCTX_API_KEY'];
  delete process.env['STRUCTX_BASE_URL'];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Construction', () => {
  it('throws when no api key is provided or in env', () => {
    expect(() => new StructX({})).toThrow(StructXError);
    expect(() => new StructX({})).toThrow(/STRUCTX_API_KEY/);
  });

  it('reads STRUCTX_API_KEY from process.env when not passed', () => {
    process.env['STRUCTX_API_KEY'] = 'sx_from_env';
    const c = new StructX({ fetch: vi.fn() });
    expect(c.apiKey).toBe('sx_from_env');
  });

  it('honors explicit apiKey over env', () => {
    process.env['STRUCTX_API_KEY'] = 'sx_env';
    const c = new StructX({ apiKey: 'sx_explicit', fetch: vi.fn() });
    expect(c.apiKey).toBe('sx_explicit');
  });

  it('defaults baseUrl to https://api.struct-x.ai with trailing slash', () => {
    const c = new StructX({ apiKey: 'sx_x', fetch: vi.fn() });
    expect(c.baseUrl).toBe('https://api.struct-x.ai/');
  });

  it('strips trailing slashes from baseUrl before re-appending', () => {
    const c = new StructX({ apiKey: 'sx_x', baseUrl: 'https://x.example///', fetch: vi.fn() });
    expect(c.baseUrl).toBe('https://x.example/');
  });

  it('accepts a RetryPolicy instance OR a partial options object', () => {
    const c1 = new StructX({ apiKey: 'sx_x', retry: new RetryPolicy({ maxAttempts: 5 }), fetch: vi.fn() });
    expect(c1.retry.maxAttempts).toBe(5);
    const c2 = new StructX({ apiKey: 'sx_x', retry: { maxAttempts: 7 }, fetch: vi.fn() });
    expect(c2.retry.maxAttempts).toBe(7);
  });

  // The runtime guard for "fetch is unavailable" in StructX's
  // constructor exists as defense-in-depth but can't be triggered
  // from a test that runs under Node 18+ — `options.fetch ??
  // globalThis.fetch` falls back to the platform fetch, which is
  // always a function on supported Node versions. The guard fires
  // only when someone runs the SDK under Node < 18 (which would
  // also hit our `engines.node: >=18` enforcement during install),
  // so the path is unreachable from a normal test environment.
});

describe('extract — happy path', () => {
  it('POSTs the body, returns the parsed Extraction', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { title: 'Widget', price_cents: 9900 },
        model_used: 'fast',
        latency_ms: 420,
        tokens: { input: 1250, output: 85 },
        credits_used: 2,
        cached: false,
      }),
    );
    const client = new StructX({ apiKey: 'sx_test', fetch: mockFetch });
    const result = await client.extract('<div>$99 Widget</div>', {
      schema: { type: 'object', properties: { title: { type: 'string' } } },
    });

    expect(result.data).toEqual({ title: 'Widget', price_cents: 9900 });
    expect(result.model_used).toBe('fast');
    expect(result.credits_used).toBe(2);

    // Confirm wire-shape — schema goes through as `schema`, not
    // `schemaDef`. tier defaults to 'required'.
    expect(mockFetch).toHaveBeenCalledOnce();
    const callArgs = mockFetch.mock.calls[0]!;
    expect(callArgs[0]).toBe('https://api.struct-x.ai/v1/extract');
    expect(callArgs[1]!.method).toBe('POST');
    const headers = callArgs[1]!.headers as Record<string, string>;
    expect(headers['X-API-Key']).toBe('sx_test');
    // src/version.ts must match package.json (bump_version.sh writes both).
    expect(headers['User-Agent']!.split(' ')[0]).toBe(`structx-sdk-node/${PACKAGE_VERSION}`);
    expect(headers['Content-Type']).toBe('application/json');
    const sentBody = JSON.parse(callArgs[1]!.body as string);
    expect(sentBody.tier).toBe('required');
    expect(sentBody.schema).toEqual({ type: 'object', properties: { title: { type: 'string' } } });
    expect(sentBody.template_slug).toBeUndefined();
  });

  it('forwards templateSlug as `template_slug` on the wire', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ data: {}, model_used: 'fast', latency_ms: 1, tokens: { input: 0, output: 0 }, credits_used: 1 }),
    );
    const client = new StructX({ apiKey: 'sx_test', fetch: mockFetch });
    await client.extract('content', { templateSlug: 'logs.stripe.event', tier: 'recommended' });
    const sent = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(sent.template_slug).toBe('logs.stripe.event');
    expect(sent.tier).toBe('recommended');
  });
});

describe('extract response id (v0.4.1 fix)', () => {
  it('decodes the id from the wire field `id`', async () => {
    // Bug 1: the backend emits the extraction id as `id`, not
    // `extraction_id`. The typed `id` field now surfaces it.
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({
        id: '8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b',
        data: {},
        model_used: 'fast',
        latency_ms: 1,
        tokens: { input: 1, output: 1 },
        credits_used: 1,
      }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.extract('x', { schema: { type: 'object' } });
    expect(result.id).toBe('8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b');
  });
});

describe('usage path + auth (v0.4.1 fix)', () => {
  it('GETs /billing/usage (no /v1) and sends Authorization: Bearer', async () => {
    // Bug 2: billing router mounts at /billing (no /v1 prefix).
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ credits_used_today: 7, credits_limit_daily: 334, tier: 'developer' }),
    );
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    const u = await client.usage();
    expect(u.credits_used_today).toBe(7);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/billing/usage');
    const headers = init!.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer jwt_t');
  });

  it('usage() without a bearer token throws up-front (no 401 round-trip)', async () => {
    const mockFetch = vi.fn();
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await expect(client.usage()).rejects.toBeInstanceOf(StructXError);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe('Error mapping by status code', () => {
  async function fail(status: number, body: Record<string, unknown> = { error: 'oops', code: 'oops' }) {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(body, status));
    const client = new StructX({
      apiKey: 'sx_test', fetch: mockFetch,
      retry: { maxAttempts: 1 }, // no retries for error-mapping tests
    });
    return client.extract('x', { schema: { type: 'object' } });
  }

  it('401 → AuthenticationError', async () => {
    await expect(fail(401)).rejects.toBeInstanceOf(AuthenticationError);
  });

  it('403 → PermissionDeniedError', async () => {
    await expect(fail(403)).rejects.toBeInstanceOf(PermissionDeniedError);
  });

  it('404 → NotFoundError', async () => {
    await expect(fail(404)).rejects.toBeInstanceOf(NotFoundError);
  });

  it('400 → ValidationError', async () => {
    await expect(fail(400)).rejects.toBeInstanceOf(ValidationError);
  });

  it('422 → ValidationError', async () => {
    await expect(fail(422)).rejects.toBeInstanceOf(ValidationError);
  });

  it('429 → RateLimitError with retryAfter from header', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse(
        { error: 'rate limited', code: 'daily_cap', credits_used: 1000, credits_remaining: 0 },
        429,
        { 'Retry-After': '60' },
      ),
    );
    const client = new StructX({ apiKey: 'sx_test', fetch: mockFetch, retry: { maxAttempts: 1 } });
    try {
      await client.extract('x', { schema: { type: 'object' } });
      throw new Error('expected to reject');
    } catch (e) {
      expect(e).toBeInstanceOf(RateLimitError);
      const rl = e as RateLimitError;
      expect(rl.statusCode).toBe(429);
      expect(rl.retryAfter).toBe(60);
      expect(rl.creditsUsed).toBe(1000);
      expect(rl.creditsRemaining).toBe(0);
      expect(rl.code).toBe('daily_cap');
    }
  });

  it('500 → ServerError', async () => {
    // 500 on a WRITE path doesn't retry, so we still see it surface.
    await expect(fail(500)).rejects.toBeInstanceOf(ServerError);
  });

  it('418 → raw ApiError (no specific subclass)', async () => {
    const e = await fail(418).catch((err) => err);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.constructor.name).toBe('ApiError');
    expect(e.statusCode).toBe(418);
  });

  it('captures requestId from x-request-id header', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ error: 'boom' }, 500, { 'x-request-id': 'req_xyz' }),
    );
    const client = new StructX({ apiKey: 'sx_test', fetch: mockFetch, retry: { maxAttempts: 1 } });
    const err = await client.extract('x', { schema: { type: 'object' } }).catch((e) => e);
    expect(err.requestId).toBe('req_xyz');
  });
});

describe('Retry behavior', () => {
  it('retries TransportError up to maxAttempts and then raises', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new TypeError('network down'));
    // usage() is JWT-only — supply a bearer token so we exercise the retry
    // loop rather than the up-front bearer guard.
    const client = new StructX({
      apiKey: 'sx_test', bearerToken: 'jwt_test', fetch: mockFetch,
      retry: { maxAttempts: 3, initialBackoff: 0.001, jitter: 0 },
    });
    await expect(client.usage()).rejects.toBeInstanceOf(TransportError);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('retries 5xx on READ paths', async () => {
    const mockFetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'boom' }, 503))
      .mockResolvedValueOnce(jsonResponse({ error: 'boom' }, 503))
      .mockResolvedValueOnce(jsonResponse({ credits_used_today: 5, credits_limit_daily: 100 }, 200));
    const client = new StructX({
      apiKey: 'sx_test', bearerToken: 'jwt_test', fetch: mockFetch,
      retry: { maxAttempts: 3, initialBackoff: 0.001, jitter: 0 },
    });
    const u = await client.usage();
    expect(u.credits_used_today).toBe(5);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('does NOT retry 5xx on WRITE paths', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ error: 'boom' }, 502));
    const client = new StructX({
      apiKey: 'sx_test', fetch: mockFetch,
      retry: { maxAttempts: 3, initialBackoff: 0.001, jitter: 0 },
    });
    await expect(client.extract('x', { schema: {} })).rejects.toBeInstanceOf(ServerError);
    // Single fetch — no retry.
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('respects Retry-After on a transient 503', async () => {
    const mockFetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: 'busy' }, 503, { 'Retry-After': '0' }))
      .mockResolvedValueOnce(jsonResponse({ credits_used_today: 1, credits_limit_daily: 100 }, 200));
    const client = new StructX({
      apiKey: 'sx_test', bearerToken: 'jwt_test', fetch: mockFetch,
      retry: { maxAttempts: 2 },
    });
    await client.usage();
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

describe('Listing endpoints', () => {
  it('listTemplates accepts both list and { templates: [...] } shapes', async () => {
    const flat = vi.fn().mockResolvedValue(jsonResponse([{ slug: 'a', name: 'A', schema: {} }]));
    const c1 = new StructX({ apiKey: 'sx_test', fetch: flat });
    expect((await c1.listTemplates()).length).toBe(1);

    const wrapped = vi.fn().mockResolvedValue(jsonResponse({ templates: [{ slug: 'b', name: 'B', schema: {} }] }));
    const c2 = new StructX({ apiKey: 'sx_test', fetch: wrapped });
    expect((await c2.listTemplates()).length).toBe(1);
  });
});

describe('Privacy: redaction reaches the actually-raised exception', () => {
  it('strips api_key from the body before attaching to the error', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse(
        { error: 'something', code: 'oops', api_key: 'sx_should_not_appear' },
        500,
      ),
    );
    const client = new StructX({ apiKey: 'sx_test', fetch: mockFetch, retry: { maxAttempts: 1 } });
    const err = await client.extract('x', { schema: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ServerError);
    const body = err.responseBody as Record<string, unknown>;
    expect(body.api_key).toMatch(/^<redacted/);
    expect(String(err)).not.toContain('sx_should_not_appear');
  });
});

// ── v0.2.0 surface — Python SDK v0.3.0 parity ────────────────
//
// These tests mirror `mcp/tests/test_dispatch_v2.py` from
// the MCP package: they assert wire shape (URL, method, body keys with
// camelCase → snake_case at the boundary) rather than response parsing.
// The wire shape is the contract we ship customers + agents through MCP.

describe('Catalog browsing (v0.2.0)', () => {
  it('listCategories GETs /v1/templates/categories and returns raw object', async () => {
    const tree = { logs: { vendors: ['stripe', 'github'] }, finance: {} };
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(tree));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.listCategories();
    expect(result).toEqual(tree);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/categories');
    expect(init!.method).toBe('GET');
    expect(init!.body).toBeUndefined();
  });

  it('getTemplate GETs /v1/templates/<slug> with url-encoded slug', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ slug: 'logs.stripe.event', name: 'Stripe Event', schema: {} }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.getTemplate('logs.stripe.event');
    const [url] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event');
  });

  it('getTemplate encodes slugs containing characters that need escaping', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ slug: 'a/b', schema: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.getTemplate('a/b');
    const [url] = mockFetch.mock.calls[0]!;
    // The `/` must be percent-encoded so it doesn't change the URL's path
    // shape — otherwise an attacker controlling the slug could traverse
    // into a sibling route.
    expect(url).toBe('https://api.struct-x.ai/v1/templates/a%2Fb');
  });
});

describe('Personal schemas — CRUD (v0.2.0)', () => {
  // These routes (/v1/schemas/mine…) are JWT-only, so every client here
  // carries a bearer token alongside its API key.
  it('listMySchemas accepts both list and { schemas: [...] } shapes', async () => {
    const flat = vi.fn().mockResolvedValue(jsonResponse([{ slug: 'a', name: 'A' }]));
    const c1 = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: flat });
    expect((await c1.listMySchemas()).length).toBe(1);

    const wrapped = vi.fn().mockResolvedValue(jsonResponse({ schemas: [{ slug: 'b' }] }));
    const c2 = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: wrapped });
    expect((await c2.listMySchemas()).length).toBe(1);
  });

  it('listMySchemas sends Authorization: Bearer', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ schemas: [] }));
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    await client.listMySchemas();
    const headers = mockFetch.mock.calls[0]![1]!.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer jwt_t');
    expect(headers['X-API-Key']).toBe('sx_t');
  });

  it('a JWT-only method with no bearer token throws up-front', async () => {
    const mockFetch = vi.fn();
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await expect(client.listMySchemas()).rejects.toBeInstanceOf(StructXError);
    // Guard fires before any network call.
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('getMySchema GETs /v1/schemas/mine/<slug>', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ slug: 'mine-a' }));
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    await client.getMySchema('mine-a');
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/schemas/mine/mine-a');
    expect(init!.method).toBe('GET');
  });

  it('saveMySchema POSTs with camelCase → snake_case mapping; defaults publishable_consent=false', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ slug: 'srv-generated' }));
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    await client.saveMySchema({
      name: 'My Schema',
      jsonSchema: { type: 'object' },
    });
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/schemas/mine');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      name: 'My Schema',
      json_schema: { type: 'object' },
      publishable_consent: false,
    });
  });

  it('saveMySchema includes optional slug / description / category / publishableConsent when set', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    await client.saveMySchema({
      name: 'My Schema',
      jsonSchema: { type: 'object' },
      slug: 'my-schema',
      description: 'Notes',
      category: 'logs',
      publishableConsent: true,
    });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({
      name: 'My Schema',
      json_schema: { type: 'object' },
      slug: 'my-schema',
      description: 'Notes',
      category: 'logs',
      publishable_consent: true,
    });
  });

  it('updateMySchema PATCHes with only the fields that were passed', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    await client.updateMySchema('my-schema', {
      description: 'Notes v2',
      jsonSchema: { type: 'object', properties: { x: { type: 'string' } } },
    });
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/schemas/mine/my-schema');
    expect(init!.method).toBe('PATCH');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      description: 'Notes v2',
      json_schema: { type: 'object', properties: { x: { type: 'string' } } },
    });
    // Critically: name + category + publishable_consent NOT in body —
    // PATCH semantics require unspecified fields to be left alone.
    expect(body.name).toBeUndefined();
    expect(body.category).toBeUndefined();
    expect(body.publishable_consent).toBeUndefined();
  });

  it('deleteMySchema sends DELETE and resolves to void', async () => {
    // 204 No Content is the realistic backend response, but WHATWG forbids
    // a non-null body on null-body-status responses (204/205/304). Use
    // `null` body to match the spec; the SDK's response.text() returns ""
    // for null bodies, so the code path is identical to a real 204.
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    const client = new StructX({ apiKey: 'sx_t', bearerToken: 'jwt_t', fetch: mockFetch });
    const result = await client.deleteMySchema('my-schema');
    expect(result).toBeUndefined();
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/schemas/mine/my-schema');
    expect(init!.method).toBe('DELETE');
    expect(init!.body).toBeUndefined();
  });
});

describe('Recommend → augment loop (v0.2.0)', () => {
  it('recommendTemplates POSTs /v1/schemas/recommend with k default 5', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ event_id: 'evt_1', recommendations: [] }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.recommendTemplates({ content: 'webhook payload' });
    expect(result.event_id).toBe('evt_1');
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({ k: 5, content: 'webhook payload' });
  });

  it('recommendTemplates honors draftSchema → draft_schema mapping and custom k', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ event_id: 'e' }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const schema = { type: 'object', properties: { id: { type: 'string' } } };
    await client.recommendTemplates({ draftSchema: schema, k: 10 });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({ k: 10, draft_schema: schema });
  });

  it('augmentSchema POSTs to the feedback endpoint with decision=augment', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ inferred: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.augmentSchema('evt_42', 'rename event_id to id');
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/schemas/recommendations/evt_42/feedback');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      decision: 'augment',
      augment_guidance: 'rename event_id to id',
    });
  });
});

describe('Community withdrawals (v0.2.0)', () => {
  it('requestWithdrawal POSTs /v1/templates/<slug>/withdraw with mode + justification', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ status: 'pending_review' }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.requestWithdrawal('logs.acme.event', {
      mode: 'fork_detach',
      justification: 'Vendor format deprecated',
    });
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.acme.event/withdraw');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      mode: 'fork_detach',
      justification: 'Vendor format deprecated',
    });
  });

  it('requestWithdrawal accepts legal_erasure mode', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.requestWithdrawal('s', { mode: 'legal_erasure', justification: 'r' });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body.mode).toBe('legal_erasure');
  });
});

// ── v0.4.0 surface — Python SDK v0.4.x parity + Core agent surface ──
//
// Same posture as the v0.2.0 block above: assert wire shape (URL,
// method, body keys with camelCase → snake_case at the boundary)
// rather than response parsing. The wire shape is the contract.

describe('Synthetic data (v0.4.0)', () => {
  it('synthesize POSTs /v1/synth/generate with defaults count=10 mode=hybrid', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ records: [{ a: 1 }], manifest: { produced: 1, anonymized: true } }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.synthesize({ templateSlug: 'logs.aws.cloudtrail' });
    expect((result.manifest as Record<string, unknown>).anonymized).toBe(true);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/synth/generate');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({ count: 10, mode: 'hybrid', template_slug: 'logs.aws.cloudtrail' });
  });

  it('synthesize forwards jsonSchema → json_schema, directives, params, count, mode', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ records: [], manifest: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.synthesize({
      jsonSchema: { type: 'object' },
      count: 5,
      directives: 'mostly denies',
      params: { locale: 'en' },
      mode: 'llm',
    });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({
      count: 5,
      mode: 'llm',
      json_schema: { type: 'object' },
      directives: 'mostly denies',
      params: { locale: 'en' },
    });
    // No template_slug when generating from an inline schema.
    expect(body.template_slug).toBeUndefined();
  });

  it('synthesize is a WRITE path — does NOT retry on 5xx', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ error: 'boom' }, 502));
    const client = new StructX({
      apiKey: 'sx_t', fetch: mockFetch,
      retry: { maxAttempts: 3, initialBackoff: 0.001, jitter: 0 },
    });
    await expect(client.synthesize({ jsonSchema: {} })).rejects.toBeInstanceOf(ServerError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

describe('Synth dataset management (v0.4.0)', () => {
  it('listDatasets accepts both array and { datasets: [...] } shapes', async () => {
    const flat = vi.fn().mockResolvedValue(jsonResponse([{ id: 'd1' }]));
    const c1 = new StructX({ apiKey: 'sx_t', fetch: flat });
    const r1 = await c1.listDatasets();
    expect(r1.length).toBe(1);
    expect(flat.mock.calls[0]![0]).toBe('https://api.struct-x.ai/v1/synth/datasets');

    const wrapped = vi.fn().mockResolvedValue(jsonResponse({ datasets: [{ id: 'd2' }, { id: 'd3' }] }));
    const c2 = new StructX({ apiKey: 'sx_t', fetch: wrapped });
    expect((await c2.listDatasets()).length).toBe(2);
  });

  it('getDataset GETs /v1/synth/datasets/<id>', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ id: 'd1', records: [] }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.getDataset('d1');
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/synth/datasets/d1');
    expect(init!.method).toBe('GET');
  });

  it('exportDataset returns the raw body string and passes ?format=', async () => {
    const csv = 'a,b\n1,2\n';
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(csv, { status: 200, headers: { 'Content-Type': 'text/csv' } }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.exportDataset('d1', 'csv');
    expect(result).toBe(csv);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/synth/datasets/d1/export?format=csv');
    expect(init!.method).toBe('GET');
  });

  it('exportDataset defaults format to jsonl', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response('{"a":1}\n', { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.exportDataset('d1');
    const [url] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/synth/datasets/d1/export?format=jsonl');
  });

  it('deleteDataset sends DELETE to /v1/synth/datasets/<id>', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ deleted: true, id: 'd1' }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.deleteDataset('d1');
    expect(result.deleted).toBe(true);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/synth/datasets/d1');
    expect(init!.method).toBe('DELETE');
    expect(init!.body).toBeUndefined();
  });
});

describe('Canonical projection (v0.4.0)', () => {
  it('listFrameworks GETs /v1/canonical/frameworks', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ frameworks: [{ framework: 'ocsf', version: '1.0', kind: 'event' }] }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.listFrameworks();
    expect((result.frameworks as unknown[]).length).toBe(1);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/canonical/frameworks');
    expect(init!.method).toBe('GET');
  });

  it('project POSTs /v1/canonical/project with extracted + frameworks + template_slug', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ projections: { ocsf: { record: {}, coverage: {}, valid: true } } }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.project({ id: 'evt_1' }, ['ocsf', 'ecs'], { templateSlug: 'logs.stripe.event' });
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/canonical/project');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      extracted: { id: 'evt_1' },
      frameworks: ['ocsf', 'ecs'],
      template_slug: 'logs.stripe.event',
    });
  });

  it('project forwards inline schema → schema (not template_slug)', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ projections: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const schema = { type: 'object', 'x-structx-concept': 'event' };
    await client.project({ a: 1 }, ['otel'], { schema });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({ extracted: { a: 1 }, frameworks: ['otel'], schema });
    expect(body.template_slug).toBeUndefined();
  });

  it('projectTemplate GETs /v1/templates/<slug>/canonical with comma-joined framework list', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ projections: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.projectTemplate('logs.stripe.event', ['ocsf', 'ecs']);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event/canonical?framework=ocsf%2Cecs');
    expect(init!.method).toBe('GET');
  });

  it('projectTemplate omits the framework param when none is given', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ projections: {} }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.projectTemplate('logs.stripe.event');
    const [url] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event/canonical');
  });
});

describe('Batch extract (v0.4.0)', () => {
  it('batchExtract POSTs /v1/extract/batch mapping each item to the wire shape', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ results: [], total_latency_ms: 12, total_credits_used: 3 }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.batchExtract(
      [
        { content: 'a', schema: { type: 'object' } },
        { content: 'b', templateSlug: 'logs.stripe.event', tier: 'recommended' },
      ],
      { options: { use_cache: false } },
    );
    expect(result.total_credits_used).toBe(3);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/extract/batch');
    expect(init!.method).toBe('POST');
    const body = JSON.parse(init!.body as string);
    expect(body).toEqual({
      items: [
        { content: 'a', tier: 'required', schema: { type: 'object' } },
        { content: 'b', tier: 'recommended', template_slug: 'logs.stripe.event' },
      ],
      options: { use_cache: false },
    });
  });

  it('batchExtract is a WRITE path — does NOT retry on 5xx', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ error: 'boom' }, 503));
    const client = new StructX({
      apiKey: 'sx_t', fetch: mockFetch,
      retry: { maxAttempts: 3, initialBackoff: 0.001, jitter: 0 },
    });
    await expect(
      client.batchExtract([{ content: 'a', schema: {} }]),
    ).rejects.toBeInstanceOf(ServerError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

describe('Template extras (v0.4.0)', () => {
  it('getTemplateCostEstimate GETs /<slug>/cost-estimate with default tier=required', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ estimated_field_count: 7, estimated_credits: 1 }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.getTemplateCostEstimate('logs.stripe.event');
    expect(result.estimated_credits).toBe(1);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event/cost-estimate?tier=required');
    expect(init!.method).toBe('GET');
  });

  it('getTemplateCostEstimate forwards an explicit tier', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.getTemplateCostEstimate('logs.stripe.event', 'extended');
    const [url] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event/cost-estimate?tier=extended');
  });

  it('listTemplateVersions GETs /<slug>/versions', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({ family_slug: 'logs.stripe.event', versions: [] }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.listTemplateVersions('logs.stripe.event');
    expect(result.family_slug).toBe('logs.stripe.event');
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toBe('https://api.struct-x.ai/v1/templates/logs.stripe.event/versions');
    expect(init!.method).toBe('GET');
  });
});
