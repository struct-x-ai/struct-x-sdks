"""structx_extract_document: a local PDF (or base64) -> POST /v1/extract/document
through the SDK's extract_file. respx-mocked; no network."""
from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from structx_sdk import StructXError

from structx_mcp._tools import ALL_TOOLS
from structx_mcp.server import _dispatch

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}
OK = {
    "id": "e1", "data": {"total": 12.5}, "confidence": 0.97, "field_confidences": [],
    "confidence_source": "model", "model_used": "fast", "latency_ms": 900,
    "tokens": {"input": 5600, "output": 120}, "credits_used": 4, "credits_remaining": 96,
}


def test_registered_with_both_inputs_documented():
    tool = next(t for t in ALL_TOOLS if t.name == "structx_extract_document")
    props = tool.inputSchema["properties"]
    assert {"file_path", "document_base64", "schema", "template_slug", "model", "max_credits"} <= set(props)
    assert props["model"]["enum"] == ["auto", "fast", "balanced", "quality"]


@respx.mock
async def test_file_path_is_read_and_sent(sdk, base_url, tmp_path):
    route = respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    f = tmp_path / "invoice.pdf"
    f.write_bytes(PDF)
    out = await _dispatch("structx_extract_document",
                          {"file_path": str(f), "schema": SCHEMA, "model": "balanced", "max_credits": 40}, sdk)
    body = json.loads(route.calls.last.request.content)
    assert base64.b64decode(body["document"]["data"]) == PDF
    assert body["model"] == "balanced" and body["max_credits"] == 40 and body["schema"] == SCHEMA
    assert out["data"] == {"total": 12.5} and out["confidence_source"] == "model"


@respx.mock
async def test_base64_input_works(sdk, base_url):
    route = respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    await _dispatch("structx_extract_document",
                    {"document_base64": base64.b64encode(PDF).decode(), "template_slug": "finance.invoice"}, sdk)
    assert json.loads(route.calls.last.request.content)["template_slug"] == "finance.invoice"


@pytest.mark.parametrize("args, message", [
    ({}, "exactly one"),
    ({"file_path": "/x.pdf", "document_base64": "JVBERi0="}, "exactly one"),
    ({"file_path": "/definitely/not/here.pdf"}, "no such file"),
    ({"document_base64": "not base64!!"}, "not valid base64"),
])
async def test_bad_arguments_are_reported_not_sent(sdk, args, message):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__regex=r".*/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
        with pytest.raises(StructXError, match=message):
            await _dispatch("structx_extract_document", args, sdk)
        assert not route.called


async def test_non_pdf_files_are_refused_before_upload(sdk, tmp_path):
    """An agent steered into 'extracting' a key or config file sends nothing."""
    secret = tmp_path / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n...")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__regex=r".*/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
        with pytest.raises(StructXError, match="only PDF"):
            await _dispatch("structx_extract_document", {"file_path": str(secret), "schema": SCHEMA}, sdk)
        with pytest.raises(StructXError, match="only PDF"):
            await _dispatch("structx_extract_document",
                            {"document_base64": base64.b64encode(b"hello").decode(), "schema": SCHEMA}, sdk)
        assert not route.called


async def test_an_sdk_without_extract_file_gets_a_clear_upgrade_message(tmp_path):
    class OldSDK:
        pass
    f = tmp_path / "a.pdf"
    f.write_bytes(PDF)
    with pytest.raises(StructXError, match="upgrade"):
        await _dispatch("structx_extract_document", {"file_path": str(f), "schema": SCHEMA}, OldSDK())


# ── Network paths never reach the filesystem (Vercel 01M8B3D3FTGHBH8N25HZG6260R) ──
from structx_mcp import server as srv  # noqa: E402


@pytest.mark.parametrize("path", [
    r"\\attacker.example\share\invoice.pdf",
    r"\\?\UNC\attacker.example\share\invoice.pdf",
    r"\\.\pipe\x",
    "//attacker.example/share/invoice.pdf",
    r"\/attacker.example\share\x.pdf",
    "file://attacker.example/share/invoice.pdf",
    "  \\\\attacker.example\\share\\x.pdf",
])
async def test_network_paths_are_refused_before_any_filesystem_call(sdk, path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("filesystem probed for a network path")
    for name in ("lstat", "stat"):
        monkeypatch.setattr(srv.os, name, boom)
    monkeypatch.setattr(srv.Path, "is_file", boom)
    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(StructXError, match="network|file: URL"):
        await _dispatch("structx_extract_document", {"file_path": path, "schema": SCHEMA}, sdk)


def test_windows_mapped_network_drive_is_refused():
    seen = []
    def remote(drive):
        seen.append(drive)
        return True
    with pytest.raises(StructXError, match="network drive"):
        srv._refuse_network_path(r"Z:\scans\invoice.pdf", os_name="nt", drive_is_remote=remote)
    assert seen == ["Z:"]
    srv._refuse_network_path(r"C:\scans\invoice.pdf", os_name="nt", drive_is_remote=lambda d: False)
    srv._refuse_network_path("/Users/me/invoice.pdf", os_name="posix", drive_is_remote=remote)  # not consulted
    assert seen == ["Z:"]


async def test_symlinks_are_not_followed(sdk, tmp_path):
    real = tmp_path / "real.pdf"
    real.write_bytes(PDF)
    link = tmp_path / "link.pdf"
    link.symlink_to(real)
    with pytest.raises(StructXError, match="link"):
        await _dispatch("structx_extract_document", {"file_path": str(link), "schema": SCHEMA}, sdk)


async def test_directories_are_refused(sdk, tmp_path):
    with pytest.raises(StructXError, match="not a regular file"):
        await _dispatch("structx_extract_document", {"file_path": str(tmp_path), "schema": SCHEMA}, sdk)


@pytest.mark.parametrize("path", [
    r"\??\UNC\attacker.example\share\invoice.pdf",   # NT namespace form, single leading backslash
    r"\invoice.pdf",                                  # root-relative
    "/attacker.example/share/x.pdf",
    "C:invoice.pdf\x00.txt",
])
def test_windows_allows_only_drive_paths_or_local_relative(path):
    with pytest.raises(StructXError):
        srv._refuse_network_path(path, os_name="nt", drive_is_remote=lambda d: False, cwd=r"C:\Users\me")


def test_windows_relative_path_needs_a_local_working_directory():
    srv._refuse_network_path("invoice.pdf", os_name="nt", drive_is_remote=lambda d: False, cwd=r"C:\Users\me")
    with pytest.raises(StructXError):
        srv._refuse_network_path("invoice.pdf", os_name="nt", drive_is_remote=lambda d: False,
                                 cwd=r"\\attacker.example\share")
    with pytest.raises(StructXError, match="network drive"):
        srv._refuse_network_path("invoice.pdf", os_name="nt", drive_is_remote=lambda d: d == "Z:", cwd=r"Z:\scans")


def test_windows_walk_refuses_a_link_part_way_along_the_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "invoice.pdf").write_bytes(PDF)
    junction = tmp_path / "junction"
    junction.symlink_to(real, target_is_directory=True)
    with pytest.raises(StructXError, match="link"):
        srv._lstat_without_following(str(junction / "invoice.pdf"), os_name="nt")
    st = srv._lstat_without_following(str(real / "invoice.pdf"), os_name="nt")
    assert st.st_size == len(PDF)
