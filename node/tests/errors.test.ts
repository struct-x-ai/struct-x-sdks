import { describe, expect, it } from 'vitest';
import {
  ApiError,
  AuthenticationError,
  ERROR_BY_STATUS,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  ServerError,
  StructXError,
  TransportError,
  ValidationError,
} from '../src/errors.js';

describe('StructXError base class', () => {
  it('preserves the constructor name on subclasses', () => {
    const e = new AuthenticationError('bad key', { statusCode: 401 });
    expect(e.name).toBe('AuthenticationError');
    expect(e).toBeInstanceOf(AuthenticationError);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toBeInstanceOf(StructXError);
    expect(e).toBeInstanceOf(Error);
  });

  it('TransportError is a sibling of ApiError, not a subclass', () => {
    const t = new TransportError('connect timeout');
    expect(t).toBeInstanceOf(TransportError);
    expect(t).toBeInstanceOf(StructXError);
    expect(t).not.toBeInstanceOf(ApiError);
  });
});

describe('ERROR_BY_STATUS map', () => {
  it.each([
    [400, ValidationError],
    [401, AuthenticationError],
    [403, PermissionDeniedError],
    [404, NotFoundError],
    [422, ValidationError],
    [429, RateLimitError],
  ])('status %i → %s', (status, cls) => {
    expect(ERROR_BY_STATUS.get(status)).toBe(cls);
  });

  it('does NOT include 500 — those are inferred by status-range check', () => {
    expect(ERROR_BY_STATUS.has(500)).toBe(false);
  });
});

describe('ApiError credential redaction', () => {
  it('redacts sensitive keys at construction time', () => {
    const err = new ApiError('boom', {
      statusCode: 500,
      responseBody: {
        // Sensitive — should be redacted.
        api_key: 'sx_abcd1234567890',
        token: 'jwt-token-here',
        authorization: 'Bearer abc',
        password: 'p@ssw0rd',
        content: 'this is some long extraction content',
        // Non-sensitive — should pass through.
        error: 'internal_error',
        code: 'something',
      },
    });
    const body = err.responseBody!;
    expect(body['api_key']).toMatch(/^<redacted \d+ chars>$/);
    expect(body['token']).toMatch(/^<redacted \d+ chars>$/);
    expect(body['authorization']).toMatch(/^<redacted \d+ chars>$/);
    expect(body['password']).toMatch(/^<redacted \d+ chars>$/);
    expect(body['content']).toMatch(/^<redacted 36 chars>$/);
    expect(body['error']).toBe('internal_error');
    expect(body['code']).toBe('something');
  });

  it('redaction is case-insensitive on keys', () => {
    const err = new ApiError('boom', {
      statusCode: 500,
      responseBody: { 'X-API-Key': 'sx_secret', PASSWORD: 'hunter2' },
    });
    expect(err.responseBody!['X-API-Key']).toMatch(/^<redacted/);
    expect(err.responseBody!['PASSWORD']).toMatch(/^<redacted/);
  });

  it('tolerates null and empty responseBody', () => {
    const e1 = new ApiError('boom', { statusCode: 500, responseBody: null });
    expect(e1.responseBody).toBeNull();
    const e2 = new ApiError('boom', { statusCode: 500, responseBody: {} });
    expect(e2.responseBody).toEqual({});
  });

  it('toString omits the body to prevent log leakage via String(err)', () => {
    const err = new ApiError('boom', {
      statusCode: 500,
      code: 'internal',
      requestId: 'req_abc',
      responseBody: { api_key: 'sx_secret' },
    });
    const str = err.toString();
    // The body must NOT appear, even in redacted form, in toString.
    expect(str).not.toContain('redacted');
    expect(str).not.toContain('api_key');
    expect(str).not.toContain('sx_secret');
    // But the diagnostic fields ARE included.
    expect(str).toContain('status=500');
    expect(str).toContain('code="internal"');
    expect(str).toContain('request_id="req_abc"');
  });
});

describe('RateLimitError extras', () => {
  it('carries retryAfter + credits fields', () => {
    const err = new RateLimitError('too many', {
      statusCode: 429,
      retryAfter: 30,
      creditsUsed: 100,
      creditsRemaining: 0,
    });
    expect(err.retryAfter).toBe(30);
    expect(err.creditsUsed).toBe(100);
    expect(err.creditsRemaining).toBe(0);
  });

  it('defaults extras to null when omitted', () => {
    const err = new RateLimitError('too many', { statusCode: 429 });
    expect(err.retryAfter).toBeNull();
    expect(err.creditsUsed).toBeNull();
    expect(err.creditsRemaining).toBeNull();
  });
});

describe('ServerError', () => {
  it('extends ApiError', () => {
    const e = new ServerError('boom', { statusCode: 502 });
    expect(e).toBeInstanceOf(ServerError);
    expect(e).toBeInstanceOf(ApiError);
  });
});
