"""Owned real subprocess fixture with externally observable lifetime and hold."""
import asyncio
import os
import sys
from pathlib import Path
from mcp.server import MCPServer
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
(root/'pid').write_text(str(os.getpid()))
server=MCPServer('Held subprocess fixture')
@server.tool(structured_output=True)
async def held_read() -> dict[str,str]:
    (root/'entered').write_text('entered')
    while not (root/'release').exists():
        await asyncio.sleep(.01)
    return {'status':'complete'}
server.run()
