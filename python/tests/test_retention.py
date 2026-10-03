import json
import pytest
from amplifier_unified_mcp.broker import Broker
C=dict(fenceId='retention',commandId='reviewed-hide',purpose='retention-hide',instanceId='instance',dataScope='private')
@pytest.mark.asyncio
async def test_real_broker_retains_unknown_tool_effect_and_held_selection(tmp_path):
    owner=Broker(tmp_path);args={'context':C,'sessions':['ahp-session:/one','ahp-session:/other']}
    try:
        with pytest.raises(ValueError):owner.retention_references(args)
        owner.db.execute('INSERT INTO commands VALUES(?,?,?,?)',('lost','ahp-session:/one','sig',json.dumps({'status':'interrupted','outcome':'unknown'})));owner.db.commit()
        assert owner.quiescence.acquire('actual-mcp',C)['held']
        assert owner.retention_references(args)=={'coverage':'complete','protected':[{'session':'ahp-session:/one','reasons':['mcp-unsettled']}],'omissions':[]}
        with pytest.raises(ValueError):owner.retention_references({**args,'sessions':['ahp-session:/one']*102})
        owner.quiescence.release('actual-mcp',C,'unknown',None)
        with pytest.raises(ValueError):owner.retention_references(args)
    finally:await owner.close()
