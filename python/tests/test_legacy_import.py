import copy
import hashlib
import json
import pytest
from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.legacy_import import canonical, sha

SCOPE = 'ahp-session:/imported'
FORMAT = 'amplifier-unified-mcp-legacy-selection/v1'


def request(value, *, scope='host', legacy=None, command='import-one'):
    source = json.dumps(value, ensure_ascii=False)
    result = {'sourceId': 'reviewed-offline-store', 'source': source, 'sourceSha256': hashlib.sha256(source.encode()).hexdigest(), 'scope': scope, 'commandId': command}
    if legacy:
        result['legacySessionId'] = legacy
    return result


def registration(**extra):
    return {'format': FORMAT, 'kind': 'registration', 'registration': {'id': 'original-server', 'name': 'Original', 'command': '/not/executed', 'args': ['--preserve'], 'env': {'API_KEY': 'ORIGINAL_KEY_VARIABLE'}, 'status': 'connected', 'accountBinding': {'issuer': 'https://issuer.example', 'subject': 'old'}, **extra}}


def artifact():
    value = {'format': FORMAT, 'kind': 'artifact', 'artifact': {'id': 'original-artifact', 'kind': 'mcp-app', 'title': 'Saved original', 'revision': 7, 'sessionId': 'native-original', 'body': {'$resource': 'old-body'}, 'mcpState': {'$resource': 'old-binding'}}, 'body': {'content': '<script>original source</script>'}, 'binding': {'serverId': 'original-server', 'tool': 'launch', 'allowedTools': ['launch'], 'operationId': 'old-launch', 'savedResult': {'$resource': 'old-result'}, 'accountIdentity': {'issuer':'https://issuer.example', 'subject':'old'}}, 'result': {'content': [{'type':'text', 'text':'original result'}]}}

    value['binding']['savedResult'] = {'$resource': sha(canonical(value['result']))}
    value['artifact']['mcpState'] = {'$resource': sha(canonical(value['binding']))}
    value['artifact']['body'] = {'$resource': sha(canonical(value['body']))}
    return value


@pytest.fixture
async def broker(tmp_path):
    b = Broker(tmp_path / 'data')
    yield b
    await b.close()


async def reviewed(b, args):
    preview = b.legacy.review(args)
    return await b.legacy.apply({**args, 'reviewDigest':preview['reviewDigest']})


async def test_registration_preserves_original_identity_disconnects_and_never_transfers_grants(broker):
    args = request(registration())
    before = broker.db.execute('SELECT count(*) FROM blobs').fetchone()[0]
    preview = broker.legacy.review(args)
    assert broker.manager.state['servers'] == []
    assert broker.db.execute('SELECT count(*) FROM blobs').fetchone()[0] == before
    imported = await broker.legacy.apply({**args,'reviewDigest':preview['reviewDigest']})
    saved = broker.manager._server('original-server')
    assert saved['command'] == '/not/executed' and saved['env'] == {'API_KEY':'ORIGINAL_KEY_VARIABLE'}
    assert saved['status'] == 'disconnected' and saved['authorization']['consent'] == 'unknown'
    assert saved['account']['status'] == 'unknown' and 'accountBinding' not in saved
    assert not broker.manager.connections and not broker.manager.oauth.sessions
    assert broker.manager.get_blob(imported['original']['$resource']) == args['source']
    assert broker.legacy.receipt(args['commandId'],'host') == imported
    with pytest.raises(ValueError,match='scope'):
        broker.legacy.receipt(args['commandId'],SCOPE)


async def test_artifact_retains_revision_source_result_mapping_and_denies_all_live_authority(broker):
    value = artifact()
    args = request(value, scope=SCOPE, legacy='native-original')
    imported = await reviewed(broker,args)
    descriptor = imported['descriptor']
    assert descriptor['id'] == 'original-artifact' and descriptor['providerRevision'] == 7
    assert len(json.dumps(descriptor).encode()) < 8192
    assert await broker.read(SCOPE,descriptor['id'],'document',revision=7) == value['body']['content']
    assert (await broker.read(SCOPE,descriptor['id'],'result',revision=7))['result'] == value['result']
    status = await broker.read(SCOPE,descriptor['id'],'status',revision=7)
    assert status['status'] == 'imported_read_only' and not status['canReconnect']
    for part in ('resources','tools'):
        with pytest.raises(ValueError,match='Imported'):
            await broker.read(SCOPE,descriptor['id'],part,revision=7)
    for action, data in [('appCall', {'expectedRevision':7,'name':'launch','arguments':{}}), ('context', {'expectedRevision':7,'context':{}})]:
        with pytest.raises(ValueError,match='Imported'):
            await broker.action(SCOPE,'smartTools.'+action,{'canvasId':descriptor['id'],**data},'denied-'+action)
    receipt = await broker.action(SCOPE,'smartTools.reconnectView',{'canvasId':descriptor['id'],'expectedBindingRevision':status['bindingRevision']},'denied-reconnect')
    assert receipt['receipt']['status'] == 'failed' and not broker.manager.connections
    assert broker.manager.get_blob(imported['original']['$resource']) == args['source']


async def test_exact_retry_survives_restart_and_changed_mapping_is_rejected(tmp_path):
    b = Broker(tmp_path/'data')
    args = request(artifact(),scope=SCOPE,legacy='native-original')
    review = b.legacy.review(args)
    exact = {**args,'reviewDigest':review['reviewDigest']}
    receipt = await b.legacy.apply(exact)
    await b.close()
    b = Broker(tmp_path/'data')
    try:
        assert (await b.legacy.apply(exact)) == {**receipt,'duplicate':True}
        assert (await b.legacy.apply({**exact,'commandId':'same-source-new-command'}))['duplicate']
        changed = {**args,'scope':'ahp-session:/other'}
        new = b.legacy.plan(changed)[2]
        with pytest.raises(ValueError,match='different reviewed'):
            await b.legacy.apply({**changed,'reviewDigest':new})
        assert not b.manager.connections
        assert b.db.execute('SELECT count(*) FROM views').fetchone()[0] == 1
        assert b.legacy.receipt(args['commandId'],SCOPE) == receipt
    finally:
        await b.close()


@pytest.mark.parametrize('change',['source','mapping','review','collision'])
async def test_changed_review_and_collisions_leave_store_unchanged(broker,change):
    args=request(artifact(),scope=SCOPE,legacy='native-original')
    preview=broker.legacy.review(args)
    exact={**args,'reviewDigest':preview['reviewDigest']}
    if change=='source':exact['source']+=' '
    if change=='mapping':exact['scope']='ahp-session:/other'
    if change=='review':exact['reviewDigest']='0'*64
    if change=='collision':
        broker.db.execute('INSERT INTO views VALUES (?,?,?,?)',('original-artifact',1,'ahp-session:/other','{}'));broker.db.commit()
    count=broker.db.execute('SELECT count(*) FROM views').fetchone()[0]
    with pytest.raises(ValueError):await broker.legacy.apply(exact)
    assert broker.db.execute('SELECT count(*) FROM views').fetchone()[0] == count
    assert broker.db.execute('SELECT count(*) FROM blobs').fetchone()[0] == 0
    assert broker.db.execute('SELECT count(*) FROM legacy_imports').fetchone()[0] == 0


async def test_transaction_failure_rolls_back_body_and_receipt(broker):
    args=request(artifact(),scope=SCOPE,legacy='native-original')
    preview=broker.legacy.review(args)
    broker.db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON legacy_imports BEGIN SELECT RAISE(ABORT, 'fixture interrupted import'); END")
    broker.db.commit()
    with pytest.raises(Exception,match='fixture interrupted'):
        await broker.legacy.apply({**args,'reviewDigest':preview['reviewDigest']})
    assert broker.db.execute('SELECT count(*) FROM blobs').fetchone()[0] == 0
    assert broker.db.execute('SELECT count(*) FROM views').fetchone()[0] == 0


@pytest.mark.parametrize('extra',[{'credentials':{'access_token':'must-not-copy'}},{'installationId':'old-installed'},{'env':{'API_KEY':'literal-secret!'}},{'headers':{'Authorization':'literal-secret!'}}])
async def test_credentials_and_unproven_package_ownership_refuse_without_writes(broker,extra):
    with pytest.raises(ValueError):broker.legacy.review(request(registration(**extra)))
    assert not broker.manager.state['servers'] and not broker.manager.connections


async def test_duplicate_json_keys_and_nonfinite_values_are_rejected(broker):
    for source in ('{"format":"x","format":"y"}', '{"format":NaN}'):
        args=request(registration());args.update(source=source,sourceSha256=hashlib.sha256(source.encode()).hexdigest())
        with pytest.raises(ValueError,match='unambiguous'):broker.legacy.review(args)


@pytest.mark.parametrize('field',['body','binding','result'])
async def test_selected_hydration_must_match_original_immutable_references(broker,field):
    value=artifact()
    if field=='body':value['body']['content']='substituted document'
    if field=='binding':value['binding']['allowedTools'].append('additional tool')
    if field=='result':value['result']['content'][0]['text']='substituted result'
    with pytest.raises(ValueError,match='content-addressed'):
        broker.legacy.review(request(value,scope=SCOPE,legacy='native-original'))
    assert broker.db.execute('SELECT count(*) FROM blobs').fetchone()[0] == 0


async def test_broker_import_intake_holds_write_but_keeps_exact_receipt_and_original_readable(broker):
    args=request(registration())
    receipt=await reviewed(broker,args)
    context=dict(fenceId='import-fence',commandId='stop',purpose='recovery',instanceId='one',dataScope='owned')
    assert broker.quiescence.acquire('mcp',context)['held']
    with pytest.raises(ValueError,match='intake is closed'):
        await broker.legacy.apply({**args,'reviewDigest':receipt['reviewDigest']})
    assert broker.legacy.receipt(args['commandId'],'host') == receipt
    first=broker.legacy.source(args['commandId'],'host',limit=10)
    assert first['source'] == args['source'][:10] and first['nextOffset'] == 10
