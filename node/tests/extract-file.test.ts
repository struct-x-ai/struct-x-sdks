import { afterEach, describe, expect, it, vi } from 'vitest';
import { RetryPolicy, ServerError, StructX, StructXError, TransportError, WRITE_PATHS } from '../src/index.js';

const PDF = new TextEncoder().encode('%PDF-1.7\n1 0 obj\n<<>>\nendobj\n');
const SCHEMA = { type: 'object', properties: { total: { type: 'number' } } };
const OK = {
  id: 'e1', data: { total: 12.5 }, confidence: 0.97, field_confidences: [],
  confidence_source: 'model', model_used: 'fast', latency_ms: 900,
  tokens: { input: 5600, output: 120 }, credits_used: 4, credits_remaining: 96,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function client(fetchImpl: typeof fetch) {
  return new StructX({
    apiKey: 'sx_test', baseUrl: 'https://api.test.struct-x.ai', fetch: fetchImpl,
    retry: new RetryPolicy({ maxAttempts: 3, initialBackoff: 0, maxBackoff: 0 }),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('extractFile', () => {
  it('sends the PDF as base64 with the options and returns the extraction', async () => {
    const f = vi.fn(async () => json(OK));
    const result = await client(f as unknown as typeof fetch).extractFile(PDF, {
      schema: SCHEMA, model: 'balanced', maxCredits: 40, fields: ['total'],
      options: { confidence_threshold: 0.8 },
    });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test.struct-x.ai/v1/extract/document');
    const body = JSON.parse(init.body as string);
    expect(body.document).toEqual({
      type: 'base64', media_type: 'application/pdf', data: Buffer.from(PDF).toString('base64'),
    });
    expect(body).toMatchObject({ schema: SCHEMA, model: 'balanced', max_credits: 40, fields: ['total'] });
    expect(body.template_slug).toBeUndefined();
    expect(result.confidence_source).toBe('model');
    expect(result.credits_used).toBe(4);
  });

  it('accepts an ArrayBuffer and a template slug', async () => {
    const f = vi.fn(async () => json(OK));
    await client(f as unknown as typeof fetch).extractFile(PDF.slice().buffer, { templateSlug: 'finance.invoice' });
    const body = JSON.parse((f.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(Buffer.from(body.document.data, 'base64')).toEqual(Buffer.from(PDF));
    expect(body.template_slug).toBe('finance.invoice');
  });

  it('is never retried on 5xx or transport errors', async () => {
    const f5 = vi.fn(async () => json({ error: 'busy', code: 'document_provider_unavailable' }, 503));
    await expect(client(f5 as unknown as typeof fetch).extractFile(PDF, { schema: SCHEMA })).rejects.toBeInstanceOf(ServerError);
    expect(f5).toHaveBeenCalledTimes(1);

    const ft = vi.fn(async () => { throw new TypeError('fetch failed'); });
    await expect(client(ft as unknown as typeof fetch).extractFile(PDF, { schema: SCHEMA })).rejects.toBeInstanceOf(TransportError);
    expect(ft).toHaveBeenCalledTimes(1);
  });

  it('rejects an empty document before sending', async () => {
    const f = vi.fn();
    await expect(client(f as unknown as typeof fetch).extractFile(new Uint8Array(), { schema: SCHEMA })).rejects.toBeInstanceOf(StructXError);
    expect(f).not.toHaveBeenCalled();
  });

  it('is a write path, and text extract keeps its retries', async () => {
    expect(WRITE_PATHS.has('/v1/extract/document')).toBe(true);
    let n = 0;
    const f = vi.fn(async () => { n += 1; if (n === 1) throw new TypeError('reset'); return json({ ...OK, confidence_source: undefined }); });
    await client(f as unknown as typeof fetch).extract('Total: 12.50', { schema: SCHEMA });
    expect(f).toHaveBeenCalledTimes(2);
  });
});
