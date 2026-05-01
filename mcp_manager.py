"""
MCP Manager — connects to external MCP servers (GitHub, etc.) via stdio.
Discovers tools from MCP servers and proxies tool calls.
"""

import os
import asyncio
import logging
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None


class MCPConnection:
    """Manages a single MCP server connection."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.session: ClientSession | None = None
        self.tools: list[dict] = []
        self._read = None
        self._write = None
        self._cm = None  # context manager
        self._session_cm = None

    async def connect(self):
        """Start the MCP server and establish a session."""
        try:
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env
            )

            self._cm = stdio_client(params)
            self._read, self._write = await self._cm.__aenter__()

            self._session_cm = ClientSession(self._read, self._write)
            self.session = await self._session_cm.__aenter__()

            await self.session.initialize()

            # Discover available tools
            tools_response = await self.session.list_tools()
            self.tools = []
            for tool in tools_response.tools:
                tool_def = {
                    "name": f"{self.config.name}__{tool.name}",
                    "description": f"[{self.config.name.upper()}] {tool.description or tool.name}",
                    "parameters": tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
                }
                self.tools.append(tool_def)

            logger.info(f"MCP [{self.config.name}] connected — {len(self.tools)} tools available")
            for t in self.tools:
                logger.info(f"  → {t['name']}")

        except Exception as e:
            if isinstance(e, FileNotFoundError):
                logger.error(
                    f"MCP [{self.config.name}] connection failed: command not found "
                    f"('{self.config.command}'). Ensure it is installed in this runtime."
                )
            else:
                logger.error(f"MCP [{self.config.name}] connection failed: {e}")
            await self.disconnect()
            raise

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on this MCP server. tool_name should be without the server prefix."""
        if not self.session:
            return f"Error: MCP server '{self.config.name}' not connected"
        try:
            result = await self.session.call_tool(tool_name, arguments)
            # Extract text content from the result
            text_parts = []
            for content in result.content:
                if hasattr(content, 'text'):
                    text_parts.append(content.text)
                else:
                    text_parts.append(str(content))
            return "\n".join(text_parts) if text_parts else "Tool returned no output"
        except Exception as e:
            logger.error(f"MCP [{self.config.name}] tool call '{tool_name}' failed: {e}")
            return f"Error calling {tool_name}: {e}"

    async def disconnect(self):
        """Shut down the MCP server connection."""
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(None, None, None)
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"MCP [{self.config.name}] disconnect error: {e}")
        finally:
            self.session = None
            self._read = None
            self._write = None
            self._cm = None
            self._session_cm = None


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self.connections: dict[str, MCPConnection] = {}

    def get_server_configs(self) -> list[MCPServerConfig]:
        """Build MCP server configs from environment variables."""
        configs = []

        # GitHub MCP Server (Docker)
        github_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        if github_token:
            configs.append(MCPServerConfig(
                name="github",
                command="docker",
                args=[
                    "run", "-i", "--rm",
                    "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
                    "ghcr.io/github/github-mcp-server"
                ],
                env={
                    **os.environ,
                    "GITHUB_PERSONAL_ACCESS_TOKEN": github_token
                }
            ))

        return configs

    async def start(self):
        """Connect to all configured MCP servers."""
        configs = self.get_server_configs()
        for config in configs:
            conn = MCPConnection(config)
            try:
                await conn.connect()
                self.connections[config.name] = conn
            except Exception as e:
                logger.error(f"Failed to start MCP server '{config.name}': {e}")

    async def shutdown(self):
        """Disconnect from all MCP servers."""
        for name, conn in self.connections.items():
            logger.info(f"Shutting down MCP [{name}]...")
            await conn.disconnect()
        self.connections.clear()

    def get_all_tools(self) -> list[dict]:
        """Get tool definitions from all connected MCP servers."""
        tools = []
        for conn in self.connections.values():
            tools.extend(conn.tools)
        return tools

    async def call_tool(self, full_tool_name: str, arguments: dict) -> str:
        """
        Call a tool by its full name (server__toolname).
        Returns the tool result as a string.
        """
        parts = full_tool_name.split("__", 1)
        if len(parts) != 2:
            return f"Error: Invalid MCP tool name format: {full_tool_name}"

        server_name, tool_name = parts
        conn = self.connections.get(server_name)
        if not conn:
            return f"Error: MCP server '{server_name}' not found or not connected"

        return await conn.call_tool(tool_name, arguments)

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to an MCP server."""
        return "__" in tool_name and tool_name.split("__")[0] in self.connections


# Global instance
mcp_manager = MCPManager()
