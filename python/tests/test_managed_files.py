import json
import pytest
from amplifier_unified_mcp.broker import Broker
C=dict(fenceId='retention',commandId='reviewed-hide',purpose='managed-files-disposal',instanceId='instance',dataScope='private')
@pytest.mark.asyncio
async def test_real_broker_retains_unknown_tool_effect_and_held_selection(tmp_path):
    owner=Broker(tmp_path);args={'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5),'context':C,'sessions':['ahp-session:/one','ahp-session:/other']}
    try:
        with pytest.raises(ValueError):owner.managed_references(args)
        owner.db.execute('INSERT INTO commands VALUES(?,?,?,?)',('lost','ahp-session:/one','sig',json.dumps({'status':'interrupted','outcome':'unknown'})));owner.db.commit()
        assert owner.quiescence.acquire('actual-mcp',C)['held']
        assert owner.managed_references(args)=={'coverage':'complete','protected':[{'session':'ahp-session:/one','reasons':['mcp-unsettled']}],'omissions':[]}
        with pytest.raises(ValueError):owner.managed_references({**args,'sessions':['ahp-session:/one']*102})
        owner.quiescence.release('actual-mcp',C,'unknown',None)
        with pytest.raises(ValueError):owner.managed_references(args)
    finally:await owner.close()

@pytest.mark.asyncio
async def test_configured_mcp_source_is_explicit_partial_coverage(tmp_path):
    owner=Broker(tmp_path,server_config={'host':'localhost','port':12345})
    await owner.manager._change(lambda state:state['servers'].append({'id':'fixture','command':'never-start','cwd':'/owned/managed/files'}))
    args={'context':C,'sessions':['ahp-session:/one'],'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5)}
    try:
        assert owner.quiescence.acquire('mcp',C)['held']
        report=owner.managed_references(args)
        assert report['coverage']=='partial' and report['omissions']==[{'reason':'mcp-server-file-dependencies-unclassified','scope':'owner'}]
        assert not owner.manager.connections
    finally:await owner.close()

@pytest.mark.asyncio
async def test_gateway_configuration_is_not_an_mcp_source_reference(tmp_path):
    owner=Broker(tmp_path,server_config={'host':'localhost','port':12345})
    args={'context':C,'sessions':['ahp-session:/one'],'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5)}
    try:
        assert owner.quiescence.acquire('mcp',C)['held']
        assert owner.managed_references(args)=={'coverage':'complete','protected':[],'omissions':[]}
    finally:await owner.close()
