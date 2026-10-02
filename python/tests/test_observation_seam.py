import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest
from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.observation_contract import local_schema

SID='ahp-session:/observation'

async def fixture(tmp_path):
    broker=Broker(tmp_path/'broker');manager=broker.manager
    installed=manager.root/'installs'/('a'*24);installed.mkdir(parents=True)
    code=installed/'observer.py';code.write_bytes((Path(__file__).parent/'fixtures/mcp_observation_server.py').read_bytes())
    manager.state['installations'].append({'id':'a'*24,'status':'installed','commit':'reviewed-source'})
    data=tmp_path/'data';data.mkdir();(data/'record.json').write_text(json.dumps({'status':'pending','revision':'r1','summary':'Exact source pending'}))
    await manager.configure({'id':'observer','name':'Fixture','command':sys.executable,'args':[str(code),str(data)],'installationId':'a'*24})
    await manager.connect('observer')
    descriptor={'installationId':'a'*24,'connectionId':'observer','toolName':'fixture_observe','implementationRevision':'reviewed-source','requestArgument':'observation','targetSchema':{'const':{'record':'one','owner':'fixture'}},'scopeSchema':{'const':{'read':'one'}},'artifacts':[{'path':'observer.py','sha256':hashlib.sha256(code.read_bytes()).hexdigest()}],'qualification':{'kind':'reviewed-trusted-read','providerFree':True,'concurrentReadSafe':True,'evidence':[{'uri':'fixture://review','digest':'a'*64}]}}
    qualified=await broker.observation(SID,'qualify',{'descriptor':descriptor})
    watch={'id':'watch1','revision':1,'sessionId':SID,'observerId':qualified['id'],'target':{'record':'one','owner':'fixture'},'scope':{'read':'one'},'sourceId':'fixture://one','args':{},'readTimeoutSeconds':3}
    return broker,qualified,watch,code,data

async def test_actual_mcp_qualified_read_is_scoped_inert_and_never_reconnects(tmp_path):
    broker,descriptor,watch,code,data=await fixture(tmp_path)
    try:
        request={'descriptor':descriptor,'watch':watch,'occurrenceId':'one'}
        value=await broker.observation(SID,'read',request);assert value['status']=='pending'
        (data/'record.json').write_text(json.dumps({'status':'actionable','revision':'r2','summary':'Exact source completed','evidence':[{'uri':'fixture://one/result','revision':'r2','digest':'a'*64}]}))
        result=await broker.observation(SID,'read',{**request,'occurrenceId':'two'});assert result['status']=='actionable'
        with pytest.raises(ValueError,match='scope'):await broker.observation('ahp-session:/other','read',request)
        await broker.manager.disconnect('observer')
        with pytest.raises(ValueError,match='not ready|not available'):await broker.observation(SID,'read',request)
        assert 'observer' not in broker.manager.connections
    finally:await broker.close()

@pytest.mark.parametrize('change',['source','schema','configuration','account','target'])
async def test_qualification_change_never_sends_under_new_authority(tmp_path,change):
    broker,descriptor,watch,code,data=await fixture(tmp_path)
    try:
        if change=='source':code.write_text(code.read_text()+'\n# Changed after qualification\n')
        elif change=='schema':broker.manager.schemas['observer'][0]['inputSchema']={'type':'object'}
        elif change=='configuration':broker.manager._server('observer')['args'].append('changed')
        elif change=='account':broker.manager._server('observer')['transport']='streamable-http'
        else:watch['target']={'record':'elsewhere'}
        with pytest.raises(Exception):await broker.observation(SID,'read',{'descriptor':descriptor,'watch':watch,'occurrenceId':'never'})
    finally:await broker.close()

def test_schema_validation_cannot_fetch_remote_authority():
    for schema in ({'$ref':'https://example.invalid/schema'},{'$dynamicRef':'#/a'},{'$id':'https://example.invalid/schema'}):
        with pytest.raises(ValueError):local_schema(schema)
