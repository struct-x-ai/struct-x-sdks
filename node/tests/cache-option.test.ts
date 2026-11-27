import { describe, expect, it, vi } from 'vitest';
import { StructX, StructXError } from '../src/index.js';

// `options.cache` is the server's name for the cache switch. Releases up to
// 0.5.0 documented `options.use_cache`, which the server ignored, so callers
// could never skip the extraction cache. The SDK now sends `cache` and still
// accepts `use_cache`, renaming it on the wire.

const EXTRACTION = {
  data: {},
  model_used: 'fast',
  latency_ms: 1,
  tokens: { input: 1, output: 1 },
  credits_used: 1,
};
const BATCH = { results: [], total_latency_ms: 1, total_credits_used: 0 };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sentOptions(mockFetch: ReturnType<typeof vi.fn>): unknown {
  const [, init] = mockFetch.mock.calls[0]!;
  return JSON.parse(init!.body as string).options;
}

describe('extract() cache option', () => {
  it.each([
    [{ use_cache: false }, { cache: false }],
    [{ use_cache: true }, { cache: true }],
    [{ cache: false }, { cache: false }],
    [{ cache: false, use_cache: false }, { cache: false }],
    [{ use_cache: null }, {}],
    [{ use_cache: false, include_citations: true }, { cache: false, include_citations: true }],
  ])('sends %j as %j', async (options, expected) => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EXTRACTION));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const original = { ...options };
    await client.extract('x', { schema: { type: 'object' }, options });
    expect(sentOptions(mockFetch)).toEqual(expected);
    expect(options).toEqual(original); // the caller's object is not changed
  });

  it('sends no options when none are given', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EXTRACTION));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.extract('x', { schema: { type: 'object' } });
    expect(sentOptions(mockFetch)).toBeUndefined();
  });

  it.each([
    [true, false],
    [false, true],
  ])('rejects cache=%s with use_cache=%s before sending', async (cache, useCache) => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EXTRACTION));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await expect(
      client.extract('x', { schema: { type: 'object' }, options: { cache, use_cache: useCache } }),
    ).rejects.toThrow(StructXError);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe('batchExtract() cache option', () => {
  it('sends use_cache as cache', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(BATCH));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.batchExtract([{ content: 'x', schema: { type: 'object' } }], {
      options: { use_cache: false },
    });
    expect(sentOptions(mockFetch)).toEqual({ cache: false });
  });

  it('rejects conflicting values before sending', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(BATCH));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await expect(
      client.batchExtract([{ content: 'x', schema: { type: 'object' } }], {
        options: { cache: true, use_cache: false },
      }),
    ).rejects.toThrow(/use_cache/);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
