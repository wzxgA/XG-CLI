"""Real subprocess smoke test for MCP stdio framing and cleanup."""

from __future__ import annotations

import asyncio
import sys

from xg.mcp.models import McpServerConfig
from xg.mcp.stdio import StdioTransport


async def test_stdio_request_notification_stderr_and_close():
    script = r'''
import json, sys
print("fixture started", file=sys.stderr, flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    result = {"method": msg["method"], "params": msg.get("params", {})}
    print(json.dumps({"jsonrpc":"2.0", "id":msg["id"], "result":result}), flush=True)
'''
    config = McpServerConfig(
        "stdio",
        "stdio",
        command=sys.executable,
        args=("-u", "-c", script),
        request_timeout=3,
        shutdown_timeout=1,
    )
    transport = StdioTransport(config)
    await transport.connect()
    result = await transport.request("ping", {"value": 1})
    assert result == {"method": "ping", "params": {"value": 1}}
    await transport.notify("notifications/initialized")
    await asyncio.sleep(0.05)
    assert any("fixture started" in line for line in transport.recent_logs())
    await transport.close()
    assert transport.process is None

