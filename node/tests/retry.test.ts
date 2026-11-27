import { describe, expect, it } from 'vitest';
import { RetryPolicy, WRITE_PATHS, isRetryableResponse } from '../src/retry.js';

describe('RetryPolicy defaults', () => {
  it('matches the Python SDK conservative defaults', () => {
    const p = new RetryPolicy();
    expect(p.maxAttempts).toBe(3);
    expect(p.initialBackoff).toBe(1.0);
    expect(p.maxBackoff).toBe(30.0);
    expect(p.retryOn5xx).toBe(true);
    expect(p.respectRetryAfter).toBe(true);
    expect(p.jitter).toBeCloseTo(0.2);
  });

  it('respects partial overrides', () => {
    const p = new RetryPolicy({ maxAttempts: 5, jitter: 0 });
    expect(p.maxAttempts).toBe(5);
    expect(p.jitter).toBe(0);
    expect(p.initialBackoff).toBe(1.0); // unchanged
  });
});

describe('RetryPolicy.backoffFor', () => {
  it('respects Retry-After when respectRetryAfter is true', () => {
    const p = new RetryPolicy({ jitter: 0 });
    expect(p.backoffFor(1, 12)).toBe(12);
  });

  it('caps Retry-After at maxBackoff', () => {
    const p = new RetryPolicy({ jitter: 0, maxBackoff: 5 });
    expect(p.backoffFor(1, 999)).toBe(5);
  });

  it('ignores Retry-After when respectRetryAfter=false', () => {
    const p = new RetryPolicy({ jitter: 0, respectRetryAfter: false });
    // Falls back to exponential — initialBackoff * 2^(1-1) = 1
    expect(p.backoffFor(1, 999)).toBe(1);
  });

  it('exponential progression with no jitter', () => {
    const p = new RetryPolicy({ jitter: 0 });
    expect(p.backoffFor(1)).toBe(1);
    expect(p.backoffFor(2)).toBe(2);
    expect(p.backoffFor(3)).toBe(4);
    expect(p.backoffFor(4)).toBe(8);
  });

  it('caps the exponential at maxBackoff', () => {
    const p = new RetryPolicy({ jitter: 0, maxBackoff: 5 });
    expect(p.backoffFor(10)).toBe(5);
  });

  it('jitter stays within the configured band', () => {
    const p = new RetryPolicy({ initialBackoff: 1, jitter: 0.5, maxBackoff: 100 });
    // 100 samples — every result must be within base ± jitterRange.
    for (let i = 0; i < 100; i++) {
      const v = p.backoffFor(2); // base = 2, range = 1 → [1, 3]
      expect(v).toBeGreaterThanOrEqual(1);
      expect(v).toBeLessThanOrEqual(3);
    }
  });
});

describe('WRITE_PATHS', () => {
  it('contains the three known mutating / billable endpoints', () => {
    expect(WRITE_PATHS.has('/v1/extract')).toBe(true);
    expect(WRITE_PATHS.has('/v1/extract/batch')).toBe(true);
    expect(WRITE_PATHS.has('/v1/schemas/infer')).toBe(true);
  });

  it('does NOT contain read paths', () => {
    expect(WRITE_PATHS.has('/v1/schemas')).toBe(false);
    expect(WRITE_PATHS.has('/v1/models')).toBe(false);
    expect(WRITE_PATHS.has('/billing/usage')).toBe(false);
  });
});

describe('isRetryableResponse', () => {
  const p = new RetryPolicy();

  it('retries 5xx on read paths', () => {
    expect(isRetryableResponse('/v1/schemas', 500, p)).toBe(true);
    expect(isRetryableResponse('/v1/schemas', 502, p)).toBe(true);
    expect(isRetryableResponse('/v1/schemas', 503, p)).toBe(true);
    expect(isRetryableResponse('/v1/schemas', 504, p)).toBe(true);
  });

  it('does NOT retry 5xx on write paths', () => {
    expect(isRetryableResponse('/v1/extract', 500, p)).toBe(false);
    expect(isRetryableResponse('/v1/extract', 503, p)).toBe(false);
    expect(isRetryableResponse('/v1/schemas/infer', 500, p)).toBe(false);
  });

  it('treats the augment path (it carries an event id) as a write path', () => {
    expect(isRetryableResponse('/v1/schemas/recommendations/evt_1/augment', 503, p)).toBe(false);
    expect(isRetryableResponse('/v1/schemas/recommendations/evt_1/feedback', 503, p)).toBe(true);
  });

  it('treats 501 as permanent, never retries it', () => {
    expect(isRetryableResponse('/v1/schemas', 501, p)).toBe(false);
  });

  it('does not retry non-5xx', () => {
    expect(isRetryableResponse('/v1/schemas', 200, p)).toBe(false);
    expect(isRetryableResponse('/v1/schemas', 429, p)).toBe(false);
    expect(isRetryableResponse('/v1/schemas', 600, p)).toBe(false);
  });

  it('respects retryOn5xx=false on the policy', () => {
    const off = new RetryPolicy({ retryOn5xx: false });
    expect(isRetryableResponse('/v1/schemas', 503, off)).toBe(false);
  });
});
