/**
 * @struct-x/sdk — Official Node.js / TypeScript SDK for struct-x.
 *
 * Quickstart:
 *
 *     import { StructX } from '@struct-x/sdk';
 *
 *     const client = new StructX({ apiKey: 'sx_...' });
 *     const result = await client.extract(
 *       '<div>$99 Widget</div>',
 *       {
 *         schema: {
 *           type: 'object',
 *           properties: {
 *             price_cents: { type: 'integer' },
 *             title: { type: 'string' },
 *           },
 *         },
 *       },
 *     );
 *     console.log(result.data);              // { price_cents: 9900, title: 'Widget' }
 *     console.log(result.field_confidences); // [{ field: 'price_cents', ... }]
 *
 * Picks up `STRUCTX_API_KEY` and `STRUCTX_BASE_URL` from process.env
 * if not passed explicitly. Zero runtime dependencies — uses native
 * fetch and AbortController. Requires Node 18+.
 */

// Client + options
export { StructX } from './client.js';
export type {
  StructXOptions,
  ExtractOptions,
  ExtractFileOptions,
  InferSchemaOptions,
  SaveMySchemaOptions,
  UpdateMySchemaFields,
  RecommendTemplatesOptions,
  RequestWithdrawalOptions,
  SynthesizeOptions,
  ProjectOptions,
  BatchExtractOptions,
  BatchExtractItem,
} from './client.js';

// Retry
export { RetryPolicy, WRITE_PATHS, isRetryableResponse } from './retry.js';
export type { RetryPolicyOptions } from './retry.js';

// Exceptions
export {
  ApiError,
  AuthenticationError,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  ServerError,
  StructXError,
  TransportError,
  ValidationError,
} from './errors.js';
export type { ApiErrorInit, RateLimitErrorInit } from './errors.js';

// Models (response types)
export type {
  Extraction,
  FieldConfidence,
  InferenceResult,
  InferredField,
  InferredSchema,
  Model,
  OpenObject,
  Recommendation,
  Template,
  TokenCounts,
  Usage,
} from './models.js';

// Meta
export { VERSION } from './version.js';
