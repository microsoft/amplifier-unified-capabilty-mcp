"""Independent MCP2 server fixture; counts durable effects, no model/accounts."""
import asyncio,json,os,sys
from pathlib import Path
from mcp.server import MCPServer
from mcp.server.apps import Apps
log,mode=map(Path,sys.argv[1:3]);apps=Apps()
def effect(amount):
 with log.open('a') as out:out.write(json.dumps({'amount':amount})+'\n');out.flush();os.fsync(out.fileno())
 return {'amount':amount}
if mode.exists() and mode.read_text()=='changed':
 @apps.tool(resource_uri='ui://fixture/app',structured_output=True)
 async def increment(amount:str='1')->dict[str,int]:return effect(int(amount))
else:
 @apps.tool(resource_uri='ui://fixture/app',structured_output=True)
 async def increment(amount:int=1)->dict[str,int]:return effect(amount)
@apps.tool(resource_uri='ui://fixture/app',structured_output=True)
async def crash_after_effect(amount:int=1)->dict[str,int]:
 effect(amount);os._exit(23)
@apps.tool(resource_uri='ui://fixture/app',structured_output=True)
async def delayed(amount:int=1)->dict[str,int]:
 await asyncio.sleep(.3);return effect(amount)
apps.add_html_resource('ui://fixture/app','<!doctype html><html><p>Saved fixture app</p></html>')
server=MCPServer('Independent fixture',extensions=[apps])
@server.resource('fixture://item/{name}',mime_type='text/plain')
def item(name:str)->str:return 'Resource '+name
server.run()
