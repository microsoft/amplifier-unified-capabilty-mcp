import asyncio
import os
import sys
from pathlib import Path
from amplifier_unified_mcp.broker import Broker
from test_connector_lifecycle import wait_for, authorize, configure_remote
from test_quiescence import context
from mcp_oauth_server import Fixture


async def test_actual_stdio_process_lifetime_is_covered_until_disconnect_completes(tmp_path):
    b=Broker(tmp_path/'state');peer=tmp_path/'peer';task=None
    try:
        await b.action('host','smartTools.configure',{'id':'owned-peer','name':'Owned peer','command':sys.executable,'args':[str(Path(__file__).parent/'fixtures/mcp_lifetime_server.py'),str(peer)]},'configure')
        assert (await b.action('host','smartTools.connect',{'id':'owned-peer'},'connect'))['receipt']['status']=='completed'
        pid=int((peer/'pid').read_text());os.kill(pid,0)
        task=asyncio.create_task(b.action('host','smartTools.call',{'id':'owned-peer','name':'held_read'},'held-read'))
        await wait_for(lambda:(peer/'entered').exists())
        refused=b.quiescence.acquire('mcp',context('held-process'))
        assert not refused['held'] and refused['activeRequests']==1
        os.kill(pid,0)  # real child is still alive while admission refuses
        (peer/'release').write_text('release')
        assert (await task)['receipt']['status']=='completed'
        assert b.quiescence.acquire('mcp',context('idle-process'))['gaps']==['connected-peer-retirement-unverified']
        await b.action('host','smartTools.disconnect',{'id':'owned-peer'},'disconnect')
        try:
            os.kill(pid,0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('SDK disconnect completed before owned subprocess exit')
        assert b.quiescence.acquire('mcp',context('exited-process'))['held']
        assert b.command_receipt('host','held-read')['status']=='completed'
    finally:
        peer.mkdir(exist_ok=True);(peer/'release').touch()
        if task:await asyncio.gather(task,return_exceptions=True)
        await b.close()


async def test_actual_http_request_refuses_fence_and_disconnect_does_not_claim_remote_server_exit(tmp_path):
    remote=Fixture(identity=True);entered=asyncio.Event();release=asyncio.Event()
    @remote.mcp.tool(structured_output=True)
    async def held_read() -> dict[str,str]:
        entered.set();await release.wait();return {'status':'complete'}
    await remote.start();b=Broker(tmp_path/'state');task=None
    try:
        await configure_remote(b.manager,remote,auth='oauth')
        await authorize(b.manager,remote)
        await wait_for(lambda:b.manager.oauth.status('remote')['phase']=='ready')
        task=asyncio.create_task(b.action('host','smartTools.call',{'id':'remote','name':'held_read'},'http-held-read'))
        await asyncio.wait_for(entered.wait(),10)
        refused=b.quiescence.acquire('mcp',context('held-http'))
        assert not refused['held'] and refused['activeRequests']==1
        release.set();assert (await task)['receipt']['status']=='completed'
        assert b.quiescence.acquire('mcp',context('idle-http'))['gaps']==['connected-peer-retirement-unverified']
        await b.action('host','smartTools.disconnect',{'id':'remote'},'http-disconnect')
        assert not remote.task.done()  # remote HTTP service belongs to fixture, not broker
        assert b.quiescence.acquire('mcp',context('disconnected-http'))['held']
        assert b.command_receipt('host','http-held-read')['status']=='completed'
    finally:
        release.set()
        if task:await asyncio.gather(task,return_exceptions=True)
        await b.close();await remote.close()
