"""struct-x Python SDK — official client for the structured-extraction API.

Quickstart:

    from structx_sdk import StructX

    client = StructX(api_key="sx_...")
    result = client.extract(
        content="<div>$99 Widget</div>",
        schema={
            "type": "object",
            "properties": {
                "price_cents": {"type": "integer"},
                "title": {"type": "string"},
            },
        },
    )
    print(result.data)               # {'price_cents': 9900, 'title': 'Widget'}
    print(result.field_confidences)  # [FieldConfidence(field='price_cents', ...)]

Async variant — same surface:

    from structx_sdk import AsyncStructX

    async with AsyncStructX(api_key="sx_...") as client:
        result = await client.extract(content="...", schema={...})
"""
from ._client import AsyncStructX, RetryPolicy, StructX
from ._exceptions import (
    ApiError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StructXError,
    TransportError,
    ValidationError,
)
from ._models import (
    BatchExtraction,
    CanonicalProjection,
    Extraction,
    ExtractionErrorItem,
    FieldConfidence,
    Framework,
    InferenceResult,
    InferredField,
    InferredSchema,
    Model,
    ProjectionOut,
    Recommendation,
    Template,
    TokenCounts,
    Usage,
)
from ._version import __version__

__all__ = [
    # Clients
    "StructX",
    "AsyncStructX",
    "RetryPolicy",
    # Exceptions
    "StructXError",
    "TransportError",
    "ApiError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    # Models
    "Extraction",
    "FieldConfidence",
    "TokenCounts",
    "InferenceResult",
    "InferredSchema",
    "InferredField",
    "Recommendation",
    "Template",
    "Model",
    "Usage",
    "BatchExtraction",
    "ExtractionErrorItem",
    "Framework",
    "ProjectionOut",
    "CanonicalProjection",
    # Meta
    "__version__",
]
