import pytest
IDENTITY=dict(installationId='installation',dataScope='private',ownerId='platform-owner',instanceId='host-one',releaseDigest='signed-release')
CONTEXT=dict(fenceId='service-fence',commandId='stop',purpose='service-stop',instanceId='host-one',dataScope='private',serviceIdentity=IDENTITY)
def proof(outcome='ready'):
    observed={**IDENTITY,'instanceId':'host-two' if outcome=='ready' else 'host-one'}
    return dict(verified=True,fenceId='service-fence',commandId='stop',outcome=outcome,instanceId=observed['instanceId'],dataScope='private',receiptId='settled',kind='service-lifecycle',serviceOutcome='resumed' if outcome=='ready' else 'stop-refused',expected=dict(IDENTITY),observed=observed,**(dict(resumeCommandId='resume',exitReceiptId='owned-exit',readyReceiptId='ready') if outcome=='ready' else dict(refusalReceiptId='no-effect')))

from amplifier_unified_mcp.broker import Broker
@pytest.mark.parametrize('field', list(IDENTITY))
async def test_service_identity_retained_and_mismatch_refuses(tmp_path,field):
    owner=Broker(tmp_path)
    try:
        assert owner.quiescence.acquire('mcp',CONTEXT)['held']
        assert owner.quiescence.inspect()['fence']['serviceIdentity']==IDENTITY
        wrong=proof();wrong['expected'][field]='foreign'
        with pytest.raises(ValueError):owner.quiescence.release('mcp',CONTEXT,'ready',wrong)
        with pytest.raises(ValueError):owner.quiescence.release('mcp',CONTEXT,'ready',{key:value for key,value in proof().items() if key not in {'kind','expected','observed'}})
    finally:await owner.close()
async def test_service_restart_and_lost_release_ack_are_exact(tmp_path):
    owner=Broker(tmp_path);owner.quiescence.acquire('mcp',CONTEXT);await owner.close();owner=Broker(tmp_path)
    try:
        assert owner.quiescence.inspect()['fence']['serviceIdentity']==IDENTITY
        with pytest.raises(ValueError):owner.quiescence.release('mcp',CONTEXT,'unchanged',{'kind':'admission-refused'})
        assert owner.quiescence.release('mcp',CONTEXT,'ready',proof())['released']
        await owner.close();owner=Broker(tmp_path)
        assert owner.quiescence.release('mcp',CONTEXT,'ready',proof())['duplicate']
        with pytest.raises(ValueError):owner.quiescence.release('mcp',CONTEXT,'ready',{**proof(),'readyReceiptId':'changed'})
        assert not owner.quiescence.inspect()['intakeClosed']
    finally:await owner.close()
async def test_service_busy_and_live_only_rollback(tmp_path):
    owner=Broker(tmp_path)
    try:
        owner.quiescence.active=1;assert not owner.quiescence.acquire('mcp',CONTEXT)['held'];owner.quiescence.active=0
        owner.quiescence.acquire('mcp',CONTEXT);assert owner.quiescence.release('mcp',CONTEXT,'unchanged',{'kind':'admission-refused'})['released']
    finally:await owner.close()
