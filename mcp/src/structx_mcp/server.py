"""MCP server runtime — wires the tool definitions in `_tools.py` to
the `structx-sdk` SDK from PyPI.

Designed for stdio transport (the Claude Desktop / Cursor / Windsurf
default). HTTP transport would be a follow-up — different deployment
model entirely.

Layering:
  - _tools.py defines TOOL METADATA (descriptions, input schemas).
  - _url_safety.py validates URLs before fetch.
  - server.py is the runtime — instantiates the SDK client once,
    routes tool calls to SDK methods, serializes responses.

Why dependency injection on the SDK client (`_make_client`): tests
substitute a fake client with `respx`-mocked HTTP. Production code
calls `_make_client()` with no args → defaults to env-driven SDK.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from structx_sdk import AsyncStructX, StructXError

from ._tools import ALL_TOOLS
from ._url_safety import UrlSafetyError, validate_fetch_url
from ._version import __version__


# Response size cap for `structx_extract_from_url`. 10MB matches typical
# web-page upper bounds; larger pages are almost certainly site dumps
# or attacks rather than real extraction targets.
_URL_FETCH_BYTE_CAP = 10 * 1024 * 1024

# Overall-fetch deadline (Phase 5.5 / φ). httpx's per-operation timeout
# doesn't bound a slow-loris server emitting 1 byte/chunk infinitely
# slowly. asyncio.wait_for around the whole fetch enforces a true
# total-time deadline. 60s is generous for a single page (95th-percentile
# load time for real pages is <10s) while bounding the worst case.
_URL_FETCH_TOTAL_TIMEOUT_S = 60.0


def _make_client() -> AsyncStructX:
    """Construct the SDK client from env. Separated from import-time
    code so tests can patch it. Raises StructXError if STRUCTX_API_KEY
    is missing."""
    return AsyncStructX(
        api_key=os.environ.get("STRUCTX_API_KEY", ""),
        base_url=os.environ.get("STRUCTX_BASE_URL"),
    )


def _check_api_key() -> None:
    """Friendly preflight — print a one-line error instead of letting
    the SDK raise a traceback at first tool call."""
    if not os.environ.get("STRUCTX_API_KEY"):
        sys.stderr.write(
            "ERROR: STRUCTX_API_KEY environment variable is required.\n"
            "Get a key at https://app.struct-x.ai/api-keys, then add it\n"
            "to your MCP-client config or shell environment.\n"
        )
        sys.exit(1)


def build_server(client: AsyncStructX | None = None) -> Server:
    """Build the MCP server. Injectable `client` for tests; defaults to
    env-driven SDK at runtime.

    Tool dispatch is a flat if/elif chain in `_dispatch`. The chain is
    long now (26 tools) but stays trivially greppable by tool name; a
    registry would add indirection without buying much. Revisit if it
    keeps growing.
    """
    server = Server(f"structx-mcp/{__version__}")
    sdk = client  # bound at call-time, see _get_sdk

    def _get_sdk() -> AsyncStructX:
        nonlocal sdk
        if sdk is None:
            sdk = _make_client()
        return sdk

    @server.list_tools()
    async def _list_tools():
        return ALL_TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            payload = await _dispatch(name, arguments, _get_sdk())
            return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]
        except StructXError as e:
            # Surface SDK errors as JSON the agent can reason about,
            # not as exceptions that vanish into the MCP transport.
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e), "type": type(e).__name__}, indent=2),
            )]
        except UrlSafetyError as e:
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e), "type": "UrlSafetyError"}, indent=2),
            )]

    return server


# Documents: a client-side cap well above the API's 20 MB, only to stop an
# accidental huge read; and the PDF magic, so an agent can't be steered into
# uploading an arbitrary local file (a key, a config) as a "document".
_DOCUMENT_MAX_BYTES = 32 * 1024 * 1024
_PDF_MAGIC = b"%PDF-"


_NETWORK_PREFIXES = ("\\\\", "//", "\\/", "/\\")  # \\host, //host and mixed forms
_DRIVE_REMOTE = 4  # GetDriveTypeW: a drive letter mapped to a network share


def _windows_drive_is_remote(drive: str) -> bool:
    """GetDriveTypeW reads the existing mapping; it does not contact the server."""
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == _DRIVE_REMOTE  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - unknown means refuse
        return True


_WIN_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _refuse_network_path(raw: str, *, os_name: str = os.name, drive_is_remote=None, cwd: str | None = None) -> None:
    r"""Refuse a path that could make the OS reach a network share. Runs on the
    string alone, before any filesystem call: on Windows even an existence
    check on \\host\share sends the user's NTLM response to that host
    (Vercel Security Review 01M8B3D3FTGHBH8N25HZG6260R). On Windows this is an
    allowlist: a drive-letter path (C:\...) or a plain relative path under a
    local working directory; UNC, \\?\, \\.\, \??\ and root-relative forms are
    all refused."""
    text = raw.strip()
    if "\x00" in text:
        raise StructXError("file_path contains a NUL character")
    if text.lower().startswith("file:"):
        raise StructXError("file_path must be a plain local path, not a file: URL")
    if text.startswith(_NETWORK_PREFIXES):
        raise StructXError("file_path must be a local file; network (UNC) and device paths are refused")
    if os_name != "nt":
        return
    if _WIN_DRIVE_PATH.match(text):
        drive = text[:2]
    else:
        base = cwd if cwd is not None else os.getcwd()
        if text.startswith(("\\", "/")) or not _WIN_DRIVE_PATH.match(base):
            raise StructXError(
                "file_path must be a local path such as C:\\Users\\you\\invoice.pdf; "
                "network, device and root-relative paths are refused"
            )
        drive = base[:2]
    if (drive_is_remote or _windows_drive_is_remote)(drive):
        raise StructXError("file_path is on a network drive; copy the PDF to a local disk first")


def _is_link(st: os.stat_result) -> bool:
    reparse = getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(st.st_mode) or bool(reparse)


def _lstat_without_following(full: str, *, os_name: str = os.name) -> os.stat_result:
    """lstat the file without following any link. On Windows every component is
    checked from the root down, so a junction or symlink part-way along the
    path (which could point at a share) is refused before anything below it
    is touched; elsewhere the final component is checked."""
    path = Path(full)
    components = [*reversed(path.parents), path] if os_name == "nt" else [path]
    st = None
    for comp in components:
        try:
            st = os.lstat(comp)
        except OSError:
            raise StructXError(f"no such file: {full}") from None
        if _is_link(st):
            raise StructXError("file_path is or goes through a link; pass the PDF's real path")
    return st


def _read_document_argument(arguments: dict[str, Any]) -> bytes:
    path = arguments.get("file_path")
    b64 = arguments.get("document_base64")
    if bool(path) == bool(b64):
        raise StructXError("give exactly one of file_path or document_base64")
    if path:
        raw = os.path.expanduser(str(path))
        _refuse_network_path(raw)
        full = os.path.abspath(raw)  # string-only: no filesystem access
        _refuse_network_path(full)
        st = _lstat_without_following(full)
        if not stat.S_ISREG(st.st_mode):
            raise StructXError(f"not a regular file: {full}")
        if st.st_size > _DOCUMENT_MAX_BYTES:
            raise StructXError("file is larger than 32 MB; the API accepts at most 20 MB (by plan)")
        with open(full, "rb") as fh:
            data = fh.read(_DOCUMENT_MAX_BYTES + 1)
    else:
        try:
            data = base64.b64decode(str(b64), validate=True)
        except (binascii.Error, ValueError):
            raise StructXError("document_base64 is not valid base64") from None
    if len(data) > _DOCUMENT_MAX_BYTES:
        raise StructXError("document is larger than 32 MB; the API accepts at most 20 MB (by plan)")
    if not data.startswith(_PDF_MAGIC):
        raise StructXError("only PDF documents are supported (the file has no %PDF- header)")
    return data


def _extract_options(options: Any) -> Any:
    """`options` for structx_extract, structx_extract_from_url and
    structx_extract_batch, with `use_cache` sent as `cache`.

    The API's name for the cache switch is `cache`. The tool descriptions up
    to 0.4.0 told agents to send `use_cache`, which the API ignored, so an
    agent could never skip the cache. Done here as well as in structx-sdk
    because the batch tool posts directly and this server may be installed
    next to an SDK release that still forwards `use_cache` unchanged.
    Different values for the two keys raise StructXError (the API answers
    422). `use_cache: null` counts as not sent.
    """
    if not isinstance(options, dict) or "use_cache" not in options:
        return options
    wire = {k: v for k, v in options.items() if k != "use_cache"}
    use_cache = options["use_cache"]
    if use_cache is None:
        return wire
    cache = wire.get("cache")
    if cache is not None and cache != use_cache:
        raise StructXError(
            "options has both 'cache' and 'use_cache' with different values; "
            "they are the same option, so send only 'cache'"
        )
    wire["cache"] = use_cache
    return wire


async def _dispatch(
    name: str,
    arguments: dict[str, Any],
    sdk: AsyncStructX,
) -> dict[str, Any]:
    """Pure-function dispatch — separate from server.py wiring so tests
    can call it directly without spinning up an MCP transport."""
    if name == "structx_extract":
        result = await sdk.extract(
            content=arguments["content"],
            schema=arguments.get("schema"),
            template_slug=arguments.get("template_slug"),
            tier=arguments.get("tier", "required"),
            options=_extract_options(arguments.get("options")),
        )
        return result.model_dump()

    if name == "structx_extract_document":
        data = _read_document_argument(arguments)
        extract_file = getattr(sdk, "extract_file", None)
        if extract_file is None:
            raise StructXError(
                "structx_extract_document needs a structx-sdk release with "
                "extract_file(); upgrade with `pip install -U structx-sdk`"
            )
        result = await extract_file(
            data,
            schema=arguments.get("schema"),
            template_slug=arguments.get("template_slug"),
            model=arguments.get("model"),
            tier=arguments.get("tier"),
            fields=arguments.get("fields"),
            max_credits=arguments.get("max_credits"),
            options=arguments.get("options"),
        )
        return result.model_dump()

    if name == "structx_infer_schema":
        result = await sdk.infer_schema(
            content=arguments["content"],
            content_type=arguments.get("content_type"),
            hints=arguments.get("hints"),
            return_recommendations=arguments.get("return_recommendations", True),
        )
        return result.model_dump()

    if name == "structx_extract_from_url":
        url = arguments["url"]
        validate_fetch_url(url)
        # Before the fetch, so a conflicting cache option costs no request.
        options = _extract_options(arguments.get("options"))
        content = await _fetch_url(url)
        result = await sdk.extract(
            content=content,
            schema=arguments.get("schema"),
            template_slug=arguments.get("template_slug"),
            tier=arguments.get("tier", "required"),
            options=options,
        )
        return result.model_dump()

    if name == "structx_list_templates":
        templates = await sdk.list_templates()
        return {"templates": [t.model_dump(by_alias=True) for t in templates]}

    if name == "structx_usage":
        usage = await sdk.usage()
        return usage.model_dump()

    # ── v0.2.0 — catalog + personal schemas + lifecycle ──────

    if name == "structx_list_categories":
        return await sdk.list_categories()

    if name == "structx_get_template":
        return await sdk.get_template(arguments["slug"])

    if name == "structx_list_my_schemas":
        return {"schemas": await sdk.list_my_schemas()}

    if name == "structx_get_my_schema":
        return await sdk.get_my_schema(arguments["slug"])

    if name == "structx_save_my_schema":
        return await sdk.save_my_schema(
            name=arguments["name"],
            json_schema=arguments["json_schema"],
            slug=arguments.get("slug"),
            description=arguments.get("description"),
            category=arguments.get("category"),
            publishable_consent=arguments.get("publishable_consent", False),
        )

    if name == "structx_update_my_schema":
        slug = arguments["slug"]
        updates = {k: v for k, v in arguments.items() if k != "slug"}
        return await sdk.update_my_schema(slug, **updates)

    if name == "structx_delete_my_schema":
        await sdk.delete_my_schema(arguments["slug"])
        return {"deleted": True, "slug": arguments["slug"]}

    if name == "structx_recommend_templates":
        return await sdk.recommend_templates(
            content=arguments.get("content"),
            draft_schema=arguments.get("draft_schema"),
            k=arguments.get("k", 5),
        )

    if name == "structx_augment_schema":
        return await sdk.augment_schema(
            arguments["event_id"], arguments["guidance"],
        )

    if name == "structx_request_withdrawal":
        return await sdk.request_withdrawal(
            arguments["slug"],
            mode=arguments["mode"],
            justification=arguments["justification"],
        )

    # ── v0.4.0 — synthetic data ──────────────────────────────

    if name == "structx_synthesize_dataset":
        return await sdk.synthesize(
            template_slug=arguments.get("template_slug"),
            json_schema=arguments.get("json_schema"),
            count=arguments.get("count", 10),
            directives=arguments.get("directives"),
            params=arguments.get("params"),
            mode=arguments.get("mode", "hybrid"),
        )

    # ── v0.3.0 — core agent surface ──────────────────────────
    #
    # The installed SDK has no dedicated methods for these endpoints
    # yet (structx-sdk 0.4.0 adds them), so we call the backend through
    # the SDK's HTTP / `_request` layer directly. This works with any
    # SDK version that exposes `_request` and is fully respx-mockable.

    if name == "structx_canonical_frameworks":
        return await sdk._request("GET", "/v1/canonical/frameworks")

    if name == "structx_canonical_project":
        body: dict[str, Any] = {
            "extracted": arguments["extracted"],
            "frameworks": arguments["frameworks"],
        }
        if arguments.get("template_slug") is not None:
            body["template_slug"] = arguments["template_slug"]
        if arguments.get("schema") is not None:
            body["schema"] = arguments["schema"]
        return await sdk._request("POST", "/v1/canonical/project", json=body)

    if name == "structx_extract_batch":
        # Thin wrapper over POST /v1/extract/batch. Per-item metering is
        # server-side; we forward items (each carries its own schema /
        # template_slug / tier) + shared options and surface the
        # credits/latency totals the backend returns.
        items: list[dict[str, Any]] = []
        for item in arguments["items"]:
            items.append({k: v for k, v in item.items() if v is not None})
        batch_body: dict[str, Any] = {"items": items}
        if arguments.get("options") is not None:
            batch_body["options"] = _extract_options(arguments["options"])
        return await sdk._request("POST", "/v1/extract/batch", json=batch_body)

    if name == "structx_template_cost_estimate":
        slug = arguments["slug"]
        return await sdk._request(
            "GET", f"/v1/templates/{slug}/cost-estimate",
            params={"tier": arguments.get("tier", "required")},
        )

    if name == "structx_template_versions":
        slug = arguments["slug"]
        return await sdk._request("GET", f"/v1/templates/{slug}/versions")

    if name == "structx_list_datasets":
        return await sdk._request(
            "GET", "/v1/synth/datasets",
            params={"limit": arguments.get("limit", 100)},
        )

    if name == "structx_get_dataset":
        dataset_id = arguments["dataset_id"]
        return await sdk._request("GET", f"/v1/synth/datasets/{dataset_id}")

    if name == "structx_export_dataset":
        # The export endpoint returns a raw file body (json / jsonl / csv),
        # NOT a JSON envelope — so we can't route it through `_request`
        # (which parses JSON and would reject a non-JSON 2xx). Use the
        # SDK's own HTTP client + URL/header helpers so auth + base_url
        # stay identical, then wrap the body for the agent.
        return await _export_dataset(
            sdk,
            arguments["dataset_id"],
            arguments.get("format", "jsonl"),
        )

    if name == "structx_delete_dataset":
        dataset_id = arguments["dataset_id"]
        return await sdk._request("DELETE", f"/v1/synth/datasets/{dataset_id}")

    raise ValueError(f"Unknown tool: {name}")


async def _export_dataset(
    sdk: AsyncStructX,
    dataset_id: str,
    fmt: str,
) -> dict[str, Any]:
    """Download a saved dataset's serialized body via the SDK's HTTP
    client. The endpoint returns a raw file (json/jsonl/csv) with a
    Content-Disposition filename, not a JSON envelope, so this bypasses
    `_request`'s JSON parsing while reusing the SDK's auth + base_url.

    Returns the body as text plus the content-type and filename the
    server chose, so the agent can save or forward the fixture."""
    response = await sdk._http.request(
        method="GET",
        url=sdk._url(f"/v1/synth/datasets/{dataset_id}/export"),
        headers=sdk._headers(),
        params={"format": fmt},
    )
    # Reuse the SDK's error mapping for non-2xx (404 owner-scope, 422
    # bad format, etc.) — it raises the typed StructXError the call_tool
    # handler already serializes for the agent.
    if not (200 <= response.status_code < 300):
        sdk._parse_response(response)
    content_disposition = response.headers.get("content-disposition", "")
    filename = None
    if "filename=" in content_disposition:
        filename = content_disposition.split("filename=", 1)[1].strip().strip('"')
    return {
        "dataset_id": dataset_id,
        "format": fmt,
        "content_type": response.headers.get("content-type"),
        "filename": filename,
        "body": response.text,
    }


async def _fetch_url(url: str) -> str:
    """Fetch a URL with size cap + overall timeout + no-redirect.

    The httpx `timeout` is per-operation (per-chunk during streaming
    reads); a malicious slow-loris server emitting 1 byte every 29s
    would never hit the per-chunk timeout but could tie up the agent
    for hours. asyncio.wait_for around the whole fetch enforces a
    total deadline, bounding the worst case regardless of chunk
    pacing. (Phase 5.5 / φ.)

    Streams the response and breaks at the size cap rather than reading
    a multi-GB body into memory. Treats response as UTF-8 with errors='
    replace' — extraction works on noisy text, no need to be strict
    about encoding.

    Validation already happened upstream in `validate_fetch_url`;
    this is the actual I/O.
    """
    async def _do_fetch() -> str:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,  # one less SSRF vector
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as fetcher:
            async with fetcher.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _URL_FETCH_BYTE_CAP:
                        raise UrlSafetyError(
                            f"Response exceeded {_URL_FETCH_BYTE_CAP // (1024*1024)}MB cap"
                        )
                    chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    try:
        return await asyncio.wait_for(_do_fetch(), timeout=_URL_FETCH_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise UrlSafetyError(
            f"Fetch exceeded {_URL_FETCH_TOTAL_TIMEOUT_S}s overall deadline "
            f"(possible slow-loris server)"
        ) from e


async def serve() -> None:
    """Run the MCP server over stdio."""
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
