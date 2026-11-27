"""Entry point invoked by the `structx-mcp` console script (defined in
pyproject.toml's [project.scripts]). Also runnable as `python -m
structx_mcp`.
"""
from __future__ import annotations

import asyncio

from .server import _check_api_key, serve


def main() -> None:
    _check_api_key()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
