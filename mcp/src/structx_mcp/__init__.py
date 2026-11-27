"""structx-mcp — Model Context Protocol server for struct-x.

Install in Claude Desktop / Cursor / Windsurf or any MCP-compatible
client and give your agent first-class structured-extraction tools.

Quickstart (Claude Desktop):

    1. pip install structx-mcp
    2. Add to ~/Library/Application Support/Claude/claude_desktop_config.json:

        {
          "mcpServers": {
            "structx": {
              "command": "structx-mcp",
              "env": { "STRUCTX_API_KEY": "sx_..." }
            }
          }
        }

    3. Restart Claude Desktop.
"""
from ._version import __version__

__all__ = ["__version__"]
