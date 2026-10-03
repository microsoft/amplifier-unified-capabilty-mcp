import asyncio
import json
import threading
from pathlib import Path
import pytest
from amplifier_unified_mcp.broker import Broker
from test_broker import setup


def context(fence='fence-one'):
    return dict(fenceId=fence,commandId='command-'+fence,purpose='recovery',instanceId='host-one',dataScope='owned-data')


def proof(c):
    return dict(verified=True,fenceId=c['fenceId'],commandId=c['commandId'],outcome='unchanged',instanceId=c['instanceId'],dataScope=c['dataScope'],receiptId='owner-release-proof')


async def test_held_owner_blocks_effects_but_preserves_passive_inspection(tmp_path):
    b=Broker(tmp_path);c=context()
    try:
        assert b.quiescence.acquire('mcp',c)['held']
        assert b.quiescence.acquire('mcp',c)['held']
        result=await b.action('host','smartTools.list',{'collection':'servers'},'read')
        assert result['read'] and not result['result']['items']
        with pytest.raises(ValueError,match='intake is closed'):
            await b.action('host','smartTools.configure',{'id':'server','name':'Server','command':'not-run'},'blocked')
        with pytest.raises(ValueError,match='intake is closed'):
            await b.oauth_callback({},'http://127.0.0.1:8941')
        with pytest.raises(ValueError,match='intake is closed'):
            await b.observation('ahp-session:/one','read',{})
        with pytest.raises(ValueError,match='intake is closed'):
            await b.read('ahp-session:/one','not-loaded','resources',revision=1)
        assert b.snapshot('host')['servers']==[]
        with pytest.raises(ValueError,match='proof'):
            b.quiescence.release('mcp',c,'unchanged',{'verified':True})
        assert b.quiescence.inspect()['intakeClosed']
        assert b.quiescence.release('mcp',c,'unchanged',proof(c))['released']
        assert b.quiescence.release('mcp',c,'unchanged',proof(c))['duplicate']
        with pytest.raises(ValueError,match='again'):b.quiescence.acquire('mcp',c)
    finally:await b.close()


async def test_restart_retains_exact_unknown_fence_without_reacquiring(tmp_path):
    c=context();b=Broker(tmp_path);assert b.quiescence.acquire('mcp',c)['held'];await b.close()
    b=Broker(tmp_path)
    try:
        assert b.quiescence.inspect()['fence']['state']=='unknown'
        with pytest.raises(ValueError,match='unknown'):b.quiescence.acquire('mcp',c)
        with pytest.raises(ValueError,match='proof'):b.quiescence.release('mcp',c,'unchanged',{'kind':'admission-refused'})
        assert b.quiescence.release('mcp',c,'unchanged',proof(c))['released']
    finally:await b.close()


async def test_idle_sdk_transport_is_a_retirement_coverage_gap_not_active_work(tmp_path):
    b=Broker(tmp_path/'state')
    try:
        act=await setup(b,tmp_path)
        first=b.quiescence.acquire('mcp',context())
        assert first['held'] is False and first['activeRequests']==0
        assert not any(first['counts'].values())
        assert first['gaps']==['connected-peer-retirement-unverified'] and first['idleTransports']==1
        assert (await act('call',{'id':'fixture','name':'increment','arguments':{'amount':1}},'still-live'))['receipt']['status']=='completed'
        assert (await act('disconnect',{'id':'fixture'},'explicit-disconnect'))['receipt']['status']=='completed'
        assert b.quiescence.acquire('mcp',context('disconnected'))['held']
        assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
    finally:await b.close()


async def test_historical_unknown_receipts_do_not_invent_current_work(tmp_path):
    b=Broker(tmp_path)
    try:
        value=json.dumps({'id':'old','status':'interrupted','outcome':'unknown'})
        b.db.execute('INSERT INTO commands VALUES(?,?,?,?)',('old','host','retained',value));b.db.commit()
        assert b.quiescence.acquire('mcp',context())['held']
        assert b.command_receipt('host','old')['outcome']=='unknown'
    finally:await b.close()


async def test_inflight_request_and_detached_oauth_are_counted_before_acquire(tmp_path):
    b=Broker(tmp_path);entered=asyncio.Event();release=asyncio.Event()
    async def command(*args,**kwargs):entered.set();await release.wait();return await original(*args,**kwargs)
    original=b.manager.command;b.manager.command=command
    work=asyncio.create_task(b.action('host','smartTools.configure',{'name':'Owned','command':'unused'},'busy'))
    try:
        await entered.wait();result=b.quiescence.acquire('mcp',context());assert not result['held'];assert result['activeRequests']==1
        release.set();await work
        task=asyncio.create_task(asyncio.Event().wait());b.manager.oauth.sessions['login']={'task':task,'public':{}}
        result=b.quiescence.acquire('mcp',context('oauth'));assert not result['held'];assert result['counts']['oauthTasks']==1
        task.cancel();await asyncio.gather(task,return_exceptions=True);b.manager.oauth.sessions.clear()
        assert b.quiescence.acquire('mcp',context('idle'))['held']
    finally:release.set();await asyncio.gather(work,return_exceptions=True);b.manager.command=original;await b.close()


async def test_cancelled_caller_does_not_hide_thread_io_still_running(tmp_path):
    b=Broker(tmp_path);entered=threading.Event();release=threading.Event()
    def run():entered.set();release.wait(timeout=10)
    work=asyncio.create_task(b.manager.background_thread(run))
    try:
        while not entered.is_set():await asyncio.sleep(.001)
        work.cancel();await asyncio.gather(work,return_exceptions=True)
        result=b.quiescence.acquire('mcp',context());assert not result['held'];assert result['counts']['backgroundIO']==1
        release.set()
        while b.manager.background_io:await asyncio.sleep(.001)
        assert b.quiescence.acquire('mcp',context('settled'))['held']
    finally:release.set();await b.close()
