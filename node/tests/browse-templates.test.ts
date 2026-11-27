/**
 * browseTemplates() pages GET /v1/templates; saveMySchema() sends
 * publishable_consent only when set; usage() reads the API's real
 * /billing/usage shape; augmentSchema() never touches /feedback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionDeniedError, StructX } from '../src/index.js';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ITEM = {
  id: '5f0c1e2a-0000-4000-8000-000000000001',
  family_slug: 'logs.stripe.event',
  version: '1.0.0',
  name: 'Stripe event',
  slug: 'logs.stripe.event',
  category: 'PAYMENTS',
  vendor: 'stripe',
  tags: ['stripe', 'payments'],
  is_first_party: true,
  default_tier: 'required',
  field_counts: { required: 9, recommended: 5, optional: 2, extended: 0, total: 16 },
  usage_count: 12,
  published_at: '2026-06-01T00:00:00Z',
};

function page(count: number, total: number, offset = 0, limit = 25) {
  return { items: Array(count).fill(ITEM), total, limit, offset };
}

function calledUrl(mockFetch: ReturnType<typeof vi.fn>): URL {
  return new URL(mockFetch.mock.calls[0]![0] as string);
}

beforeEach(() => {
  delete process.env['STRUCTX_API_KEY'];
  delete process.env['STRUCTX_BASE_URL'];
  delete process.env['STRUCTX_BEARER_TOKEN'];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('browseTemplates', () => {
  it('defaults to limit 25, offset 0 and no filters on GET /v1/templates', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(page(25, 60)));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.browseTemplates();
    const url = calledUrl(mockFetch);
    expect(url.pathname).toBe('/v1/templates');
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '25', offset: '0' });
    const init = mockFetch.mock.calls[0]![1]!;
    expect(init.method).toBe('GET');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('sx_t');
    expect(result.items).toHaveLength(25);
    expect(result.items[0]!.slug).toBe('logs.stripe.event');
    expect(result.items[0]!['schema']).toBeUndefined();
    expect(result.total).toBe(60);
    expect(result.next_offset).toBe(25);
  });

  it('sends every filter and the paging params', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(page(10, 60, 50, 50)));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.browseTemplates({
      q: 'stripe payments',
      category: 'PAYMENTS',
      vendor: 'stripe',
      tag: 'input:document',
      firstParty: false,
      limit: 50,
      offset: 50,
    });
    expect(Object.fromEntries(calledUrl(mockFetch).searchParams)).toEqual({
      q: 'stripe payments',
      category: 'PAYMENTS',
      vendor: 'stripe',
      tag: 'input:document',
      first_party: 'false',
      limit: '50',
      offset: '50',
    });
    expect(result.next_offset).toBeNull();
  });

  it('leaves out empty-string filters', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(page(0, 0)));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const result = await client.browseTemplates({ q: '', category: '' });
    expect(Object.fromEntries(calledUrl(mockFetch).searchParams)).toEqual({ limit: '25', offset: '0' });
    expect(result.next_offset).toBeNull();
  });

  it.each([
    [25, 60, 0, 25],
    [25, 60, 25, 50],
    [10, 60, 50, null],
    [0, 60, 60, null],
  ] as const)('next_offset for %i items, total %i, offset %i is %s', async (count, total, offset, expected) => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(page(count, total, offset)));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    expect((await client.browseTemplates({ offset })).next_offset).toBe(expected);
  });

  it('listTemplates still returns the whole gallery from /v1/schemas', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse([{ slug: 'a', name: 'A', schema: {} }]));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const all = await client.listTemplates();
    expect(calledUrl(mockFetch).pathname).toBe('/v1/schemas');
    expect(all[0]!.slug).toBe('a');
  });
});

describe('publishable_consent', () => {
  it.each([true, false])('saveMySchema sends publishable_consent=%s when set', async (value) => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ slug: 'a' }, 201));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.saveMySchema({ name: 'A', jsonSchema: { type: 'object' }, publishableConsent: value });
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body.publishable_consent).toBe(value);
  });

  it('updateMySchema leaves publishable_consent out unless set', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse({ slug: 'a' }));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.updateMySchema('a', { name: 'B' });
    expect(JSON.parse(mockFetch.mock.calls[0]![1]!.body as string)).toEqual({ name: 'B' });
  });

  it('a portal-only rejection surfaces as PermissionDeniedError with its code', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse(
        { error: 'Set publishable_consent in the dashboard.', code: 'publishable_consent_portal_only' },
        403,
      ),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const err = await client
      .saveMySchema({ name: 'A', jsonSchema: { type: 'object' }, publishableConsent: true })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(PermissionDeniedError);
    expect((err as PermissionDeniedError).code).toBe('publishable_consent_portal_only');
  });
});

describe('usage', () => {
  // The fields GET /billing/usage returns (the API's UsageSummary model).
  const USAGE = {
    period: '2026-11-27',
    tier: 'developer',
    extractions: 4,
    extractions_today: 4,
    credits_used: 30,
    credits_limit: 334,
    credits_remaining: 304,
    credits_used_today: 30,
    credits_limit_today: 334,
    credits_remaining_today: 304,
    extractions_this_month: 51,
    credits_used_this_month: 410,
    credits_limit_this_month: 10020,
    period_start: null,
    period_end: null,
  };

  it('returns the real fields and fills the old credits_limit_daily', async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(USAGE));
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    const u = await client.usage();
    expect(u.credits_used_today).toBe(30);
    expect(u.credits_limit_today).toBe(334);
    expect(u.credits_remaining_today).toBe(304);
    expect(u.credits_limit_this_month).toBe(10020);
    expect(u.tier).toBe('developer');
    expect(u.credits_limit_daily).toBe(334);
  });
});

describe('augmentSchema', () => {
  it('posts to /augment, never /feedback', async () => {
    const eventId = '8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b';
    const mockFetch = vi.fn().mockResolvedValue(
      jsonResponse({
        event_id: '0b5e2a91-77c4-4f0e-9d3a-5c1e2f8b6a70',
        inferred: { json_schema: { type: 'object' }, fields: [], overall_confidence: 0.9 },
        recommendations: [],
        credits_used: 4,
      }),
    );
    const client = new StructX({ apiKey: 'sx_t', fetch: mockFetch });
    await client.augmentSchema(eventId, 'drop trace_id', 'sample text');
    expect(mockFetch).toHaveBeenCalledOnce();
    const path = calledUrl(mockFetch).pathname;
    expect(path).toBe(`/v1/schemas/recommendations/${eventId}/augment`);
    expect(path).not.toContain('/feedback');
    const body = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string);
    expect(body).toEqual({ guidance: 'drop trace_id', content: 'sample text' });
    expect(body.decision).toBeUndefined();
  });
});
