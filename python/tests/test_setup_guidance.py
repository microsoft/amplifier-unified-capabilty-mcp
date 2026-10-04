"""Exact retained Git documentation reads through the real scoped broker."""
import asyncio
import copy
import hashlib
import json
import subprocess

import pytest
from jsonschema import ValidationError

from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.smart_tools import _manifest

SCOPE = 'ahp-session:/setup-fixture'

@pytest.fixture
async def fixture(tmp_path):
    broker = Broker(tmp_path / 'broker')
    identity = 'a' * 24
    source = broker.manager.root / 'installs' / identity / 'source'
    source.mkdir(parents=True)
    package = source / 'tools/example'
    package.mkdir(parents=True)
    text = "---\nsmart_tool_format: 1\nname: example\nversion: '1'\ndescription: Fixture\nrequires:\n  - name: model-provider\n    optional: true\n    purpose: Generation needs explicitly configured credentials; retained reads do not.\n---\n# CLI and optional MCP setup 雪😀\n\nRead-only help works without credentials.\n" + ('Retained guidance.\n' * 1000)
    descriptor = {'manifest': 'SMART_TOOL.md', 'cli_argv': ['example'], 'deterministic_smoke': ['manifest']}
    (package / 'SMART_TOOL.md').write_text(text)
    (package / 'smart-tool.json').write_text(json.dumps(descriptor))
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
    git('add', '.'); git('commit', '-qm', 'Retained documentation')
    commit=git('rev-parse','HEAD')
    identity=hashlib.sha256(json.dumps(['https://example.invalid/example',commit,'tools/example',[]]).encode()).hexdigest()[:24]
    destination=broker.manager.root/'installs'/identity/'source'
    destination.parent.mkdir()
    source.rename(destination);source=destination;package=source/'tools/example'
    row = {'id':identity,'repository':'https://example.invalid/example','ref':'main','path':'tools/example',
           'commit':commit,'sourceDir':str(package),'binDir':str(source.parent/'venv/bin'),
           'descriptor':descriptor,'manifest':_manifest(text),'status':'installed'}
    await broker.manager._change(lambda state: state['installations'].append(row))
    yield broker, row, text, source
    await broker.close(); broker.manager.db.close()

async def setup(broker, row, **args):
    return (await broker.action(SCOPE, 'smartTools.setup', {'id':row['id'], **args}, 'passive'))['result']

async def test_complete_exact_paged_source_not_worktree_and_no_receipt_or_state_changes(fixture):
    b,row,text,source=fixture
    original=copy.deepcopy(b.manager.state); revision=b.manager.revision
    (source/'tools/example/SMART_TOOL.md').write_text('changed worktree; never read')
    pages=[];offset=0
    while True:
        page=await setup(b,row,offset=offset,limit=1300)
        assert page['manifest']['sha256']==hashlib.sha256(text.encode()).hexdigest()
        assert page['source']['commit']==row['commit']
        assert page['readiness']=='not_assessed' and page['cli']['verification']=='not_run'
        assert page['cli']['declaredArgv']==['example'] and page['cli']['deterministicSmoke']==['manifest']
        pages.append(page['manifest']['text']);offset=page['manifest']['nextOffset']
        if offset is None:break
    assert ''.join(pages)==text
    assert b.manager.state==original and b.manager.revision==revision
    assert b.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]==0
    assert not b.manager.connections

async def test_same_passive_result_for_ui_and_agent_while_held(fixture):
    b,row,_,_=fixture
    assert b.quiescence.acquire('mcp', {'fenceId':'fixture-fence','commandId':'hold','purpose':'recovery','instanceId':'fixture','dataScope':'fixture'})['held']
    ui=await b.action(SCOPE,'smartTools.setup',{'id':row['id']},'ui',origin='ui')
    agent=await b.action(SCOPE,'smartTools.setup',{'id':row['id']},'agent',origin='agent')
    assert ui==agent and ui['read'] is True

async def test_missing_source_and_changed_receipt_refuse_without_reinstall_or_mutation(fixture):
    b,row,_,source=fixture
    stored=b.manager.state['installations'][0]
    for key,value in [('commit',None),('commit','0'*40),('sourceDir','/outside'),('descriptor',{'manifest':'../private','cli_argv':['example']}),('manifest',{'name':'other'})]:
        original=copy.deepcopy(stored);stored[key]=value
        with pytest.raises(ValueError):await setup(b,row)
        assert stored[key]==value and not b.manager.connections
        stored.clear();stored.update(original)
    original=copy.deepcopy(stored);source.rename(source.with_name('retained-original'))
    with pytest.raises((ValueError,FileNotFoundError)):await setup(b,row)
    assert stored==original

async def test_symlink_source_and_oversized_git_blob_are_refused(fixture,monkeypatch):
    b,row,_,source=fixture
    source.rename(source.with_name('original'));source.symlink_to(source.with_name('original'),target_is_directory=True)
    with pytest.raises(ValueError,match='app-owned'):await setup(b,row)
    source.unlink();source.with_name('original').rename(source)
    original=b.manager._run; calls=[]
    async def run(argv,**kwargs):
        calls.append(argv)
        if argv[1:3]==['cat-file','-s']:return '256001'
        return await original(argv,**kwargs)
    monkeypatch.setattr(b.manager,'_run',run)
    with pytest.raises(ValueError,match='limit'):await setup(b,row)
    assert not any(cmd[1]=='show' for cmd in calls)

async def test_cancelled_document_read_keeps_real_background_lifetime(fixture,monkeypatch):
    b,row,_,_=fixture
    started=asyncio.Event();release=asyncio.Event();original=b.manager._run
    async def run(argv,**kwargs):
        if argv[1:3]==['cat-file','-s']:
            started.set();await release.wait()
        return await original(argv,**kwargs)
    monkeypatch.setattr(b.manager,'_run',run)
    task=asyncio.create_task(setup(b,row));await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert len(b.manager.background_io)==1 and b.quiescence_work()['counts']['backgroundIO']==1
    release.set();await asyncio.gather(*b.manager.background_io);await asyncio.sleep(0)
    assert not b.manager.background_io

@pytest.mark.parametrize('args',[{'offset':-1},{'limit':16001},{'credentials':'secret'},{'offset':True}])
async def test_unadvertised_or_unbounded_requests_refuse(fixture,args):
    b,row,_,_=fixture
    with pytest.raises(ValidationError):await setup(b,row,**args)

async def test_redaction_is_explicit_while_hash_and_offsets_still_bind_original(fixture,monkeypatch):
    b,row,text,_=fixture
    monkeypatch.setenv('SETUP_TEST_CREDENTIAL','Retained guidance.')
    b.manager.state['servers'].append({'id':'redaction-only','name':'Redaction fixture','env':{'TOKEN':'SETUP_TEST_CREDENTIAL'}})
    value=await setup(b,row)
    assert value['manifest']['redacted'] is True
    assert 'Retained guidance.' not in value['manifest']['text']
    assert value['manifest']['sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert value['manifest']['total']==len(text)

async def test_retained_identity_rejects_valid_git_revision_substitution(fixture):
    b,row,_,source=fixture
    subprocess.run(['git','-C',str(source),'commit','--allow-empty','-qm','Another valid revision'],check=True,capture_output=True)
    commit=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],check=True,capture_output=True).stdout.decode().strip()
    b.manager.state['installations'][0]['commit']=commit
    with pytest.raises(ValueError,match='retained identity'):await setup(b,row)
