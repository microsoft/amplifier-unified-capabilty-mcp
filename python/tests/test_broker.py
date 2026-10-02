import asyncio,copy,json,sys
from pathlib import Path
import pytest
from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.mcp_account import parse_result
SCOPE='ahp-session:/fixture-one';OTHER='ahp-session:/fixture-two'
@pytest.fixture
async def broker(tmp_path):
 b=Broker(tmp_path/'state');yield b;await b.close()
async def setup(b,tmp_path):
 async def action(name,args,id):return await b.action(SCOPE,'smartTools.'+name,args,id)
 configured=await action('configure',{'id':'fixture','name':'Fixture','command':sys.executable,'args':[str(Path(__file__).parent/'fixtures/server.py'),str(tmp_path/'calls.jsonl'),str(tmp_path/'mode')]},'configure')
 assert configured['receipt']['status']=='completed',configured
 connected=await action('connect',{'id':'fixture'},'connect');assert connected['receipt']['status']=='completed',connected
 return action
async def opened(b,act):
 result=await act('call',{'id':'fixture','name':'increment','arguments':{'amount':2},'catalogRevision':b.manager._server('fixture')['catalogRevision']},'launch')
 assert result['receipt']['status']=='completed',result
 result=await act('open',{'id':'fixture','tool':'increment','operationId':'launch'},'open')
 assert result['receipt']['status']=='completed',result
 return result['descriptor']
async def test_real_sdk_open_resources_binding_and_no_replay(broker,tmp_path):
 act=await setup(broker,tmp_path);view=await opened(broker,act);identity=view['id']
 assert len(json.dumps(view))<8192
 body=await broker.read(SCOPE,identity,'body',revision=1);assert body['mcp']['toolArguments']=={'amount':2}
 html=await broker.read(SCOPE,identity,'document',revision=1);assert 'Saved fixture app' in html
 assert (await broker.read(SCOPE,identity,'status',revision=1))['status']=='ready'
 resources=await broker.read(SCOPE,identity,'resources',revision=1,arguments={'kind':'read','uri':'fixture://item/one'});assert resources['contents'][0]['text']=='Resource one'
 args={'canvasId':identity,'expectedRevision':1,'name':'increment','arguments':{'amount':3}}
 first=await act('appCall',args,'app-call');assert first['receipt']['status']=='completed',first
 second=await act('appCall',args,'app-call');assert second['duplicate']
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==2
 with pytest.raises(ValueError,match='another request'):await act('appCall',{**args,'arguments':{'amount':9}},'app-call')
 with pytest.raises(ValueError,match='not available'):await broker.read(OTHER,identity,'document',revision=1)
 with pytest.raises(ValueError,match='not accessible'):await broker.read(OTHER,'launch','operation')
 with pytest.raises(ValueError,match='not granted'):await act('appCall',{**args,'name':'ungranted'},'bad-tool')
 assert broker.db.execute("SELECT count(*) FROM commands WHERE id='bad-tool'").fetchone()[0]==0
async def test_restart_reconnect_retains_immutable_document_and_never_replays(tmp_path):
 b=Broker(tmp_path/'state');act=await setup(b,tmp_path);view=await opened(b,act);identity=view['id'];await b.close()
 b=Broker(tmp_path/'state')
 try:
  assert b.manager.connections=={} and b.manager.schemas=={}
  assert (await b.read(SCOPE,identity,'status',revision=1))['status']=='disconnected'
  assert 'Saved fixture' in await b.read(SCOPE,identity,'document',revision=1)
  status=await b.read(SCOPE,identity,'status',revision=1)
  result=await b.action(SCOPE,'smartTools.reconnectView',{'canvasId':identity,'expectedBindingRevision':status['bindingRevision']},'reconnect-view')
  assert result['receipt']['status']=='completed',result
  assert result['descriptor']['providerRevision']==2
  assert (await b.read(SCOPE,identity,'status',revision=1))['status']=='saved_version'
  assert (await b.read(SCOPE,identity,'status',revision=2))['status']=='ready'
  assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
  assert await b.read(SCOPE,identity,'document',revision=1)==await b.read(SCOPE,identity,'document',revision=2)
 finally:await b.close()
@pytest.mark.parametrize('change',['schema','resource','grant','configuration','account'])
async def test_changed_authority_preserves_source_and_denies_effect(broker,tmp_path,change):
 act=await setup(broker,tmp_path);view=await opened(broker,act);identity=view['id'];server=broker.manager._server('fixture');tools=broker.manager.schemas['fixture']
 if change=='schema':tools[0]['inputSchema']={'type':'object','properties':{'changed':{'type':'boolean'}}}
 elif change=='resource':next(t for t in tools if t['name']=='increment')['_meta']['ui']['resourceUri']='ui://other'
 elif change=='grant':tools[0].setdefault('_meta',{})['ui']={'visibility':['model']}
 elif change=='configuration':server['command']='different'
 else:server['accountBinding']={'issuer':'https://issuer.example','subject':'different'}
 status=await broker.read(SCOPE,identity,'status',revision=1);assert not status['canReconnect'],status
 with pytest.raises(ValueError):await act('appCall',{'canvasId':identity,'expectedRevision':1,'name':'increment','arguments':{}},'denied')
 assert 'Saved fixture' in await broker.read(SCOPE,identity,'document',revision=1)
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
async def test_transport_loss_after_effect_is_durable_unknown_not_replayed(broker,tmp_path):
 act=await setup(broker,tmp_path);view=await opened(broker,act)
 args={'canvasId':view['id'],'expectedRevision':1,'name':'crash_after_effect','arguments':{'amount':7}}
 result=await act('appCall',args,'crash');assert result['receipt']['status']=='interrupted',result
 assert result['receipt']['outcome']=='unknown' and result['receipt']['requestState']=='dispatched'
 retry=await act('appCall',args,'crash');assert retry['duplicate'] and retry['receipt']['outcome']=='unknown'
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==2
async def test_binding_rechecked_at_actual_dispatch(broker,tmp_path):
 act=await setup(broker,tmp_path);view=await opened(broker,act)
 # A schema/config change discovered after admission must not gain authority.
 old=broker.manager.connections['fixture'].account_guard
 async def guard(client):
  if old:await old(client)
  broker.manager._server('fixture')['accountBinding']={'issuer':'https://example.test','subject':'changed'}
 broker.manager.connections['fixture'].account_guard=guard
 result=await act('appCall',{'canvasId':view['id'],'expectedRevision':1,'name':'increment','arguments':{}},'raced')
 assert result['receipt']['status']=='failed' and result['receipt']['outcome']=='not_sent',result
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
async def test_snapshot_uses_receipt_metadata_not_large_blobs(broker,tmp_path,monkeypatch):
 act=await setup(broker,tmp_path)
 result={'content':[{'type':'text','text':'large'*100000}]}
 broker.manager.persist_operation({'id':'retained','status':'completed','result':result});broker.db.execute('INSERT INTO commands VALUES (?,?,?,?)',('retained',SCOPE,'hash',json.dumps({'id':'retained','status':'completed'})));broker.db.commit()
 def forbidden(*a,**k):raise AssertionError('List must not hydrate operation blobs')
 monkeypatch.setattr(broker.manager,'get_blob',forbidden)
 snap=broker.snapshot(SCOPE);assert len(json.dumps(snap))<100000;assert '$resource' in json.dumps(snap)
async def test_oauth_redirect_and_duplicate_callback_checks(broker):
 oauth=broker.manager.oauth
 assert oauth.origin('http://127.0.0.1:8941')=='http://127.0.0.1:8941'
 with pytest.raises(ValueError):oauth.origin('https://attacker.example')
 from multidict import MultiDict
 with pytest.raises(ValueError):await oauth.callback(MultiDict([('state','a'),('state','b'),('code','x')]),'http://127.0.0.1:8941')
 assert not broker.manager.connections
async def test_reopening_same_launch_preserves_artifact_identity_without_calling(broker,tmp_path):
 act=await setup(broker,tmp_path);view=await opened(broker,act)
 again=await act('open',{'id':'fixture','tool':'increment','operationId':'launch'},'reopen')
 assert again['descriptor']['id']==view['id'] and again['descriptor']['providerRevision']==2
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
 assert await broker.read(SCOPE,view['id'],'document',revision=1)==await broker.read(SCOPE,view['id'],'document',revision=2)

async def test_account_accept_exact_review_and_resource_attestation(broker):
 await broker.manager.configure({'id':'remote','name':'Remote','transport':'streamable-http','url':'https://fixture.example/mcp','auth':'oauth'})
 server=broker.manager._server('remote');server['accountBinding']={'issuer':'https://issuer.example','subject':'old'}
 candidate={'issuer':'https://issuer.example','subject':'new'}
 await broker.manager.accounts.changed(server,{'status':'changed','candidate':candidate})
 revision=server['accountRevision'];await broker.manager.accounts.changed(server,{'status':'changed','candidate':candidate});assert server['accountRevision']==revision
 with pytest.raises(ValueError):await broker.manager.accounts.accept({'id':'remote',**candidate,'expectedRevision':revision-1},'ui')
 result=await broker.manager.accounts.accept({'id':'remote',**candidate,'expectedRevision':revision},'ui')
 assert result['connected'] is False and server['account']['status']=='accepted' and not broker.manager.connections
 good={'contents':[{'uri':'account://who','mimeType':'application/json','text':json.dumps({'schemaVersion':1,**candidate})}]}
 assert parse_result(good,'account://who','https://issuer.example')==candidate
 with pytest.raises(ValueError):parse_result(good,'account://wrong','https://issuer.example')
 with pytest.raises(ValueError):parse_result(good,'account://who','https://attacker.example')
async def test_agent_app_call_keeps_model_visibility_even_with_saved_app_grant(broker,tmp_path):
 from test_extracted_manager import FIXTURE
 await broker.action(SCOPE,'smartTools.configure',{'id':'board','name':'Board','command':sys.executable,'args':[str(FIXTURE),str(tmp_path/'board')]},'board-config')
 await broker.action(SCOPE,'smartTools.connect',{'id':'board'},'board-connect')
 opened=await broker.action(SCOPE,'smartTools.open',{'id':'board','tool':'board_set'},'board-open')
 view=opened['descriptor'];args={'canvasId':view['id'],'expectedRevision':1,'name':'view_only','arguments':{}}
 agent=await broker.action(SCOPE,'smartTools.appCall',args,'agent-view-call',origin='agent')
 assert agent['receipt']['status']=='failed' and agent['receipt']['outcome']=='not_sent',agent
 ui=await broker.action(SCOPE,'smartTools.appCall',args,'ui-view-call',origin='ui')
 assert ui['receipt']['status']=='completed',ui
async def test_receipt_commit_does_not_rewrite_or_serialize_catalogs(broker,tmp_path):
 act=await setup(broker,tmp_path)
 class Untouched(list):
  def __iter__(self):raise AssertionError('Receipt must not serialize an unrelated catalog')
 broker.manager.state['catalog']=Untouched()
 result=await act('call',{'id':'fixture','name':'increment','arguments':{'amount':1}},'scoped-cost')
 assert result['receipt']['status']=='completed',result
async def test_explicit_metadata_pages_and_result_reads_do_not_create_work(broker,tmp_path):
 act=await setup(broker,tmp_path)
 count=broker.db.execute('SELECT count(*) FROM commands').fetchone()[0]
 result=await act('list',{'collection':'servers','offset':0,'limit':1},'read-page')
 assert result['read'] and result['result']['items'][0]['id']=='fixture'
 read=await act('readResult',{'operationId':'connect'},'read-result')
 assert read['read'] and read['result']['operationId']=='connect'
 assert broker.db.execute('SELECT count(*) FROM commands').fetchone()[0]==count
async def test_tool_owned_resource_looking_payload_is_literal(broker):
 payload={'$resource':'not-authority','value':'literal'}
 broker.manager.persist_operation({'id':'literal','status':'completed','result':payload});broker.db.commit()
 assert broker.manager.operation('literal')['result']==payload
 large={'$resource':'not-authority','value':'x'*50000}
 broker.manager.persist_operation({'id':'large-literal','status':'completed','result':large});broker.db.commit()
 assert broker.manager.operation('large-literal')['result']==large
async def test_missing_document_declared_unavailable_without_replacing_reference(broker,tmp_path):
 act=await setup(broker,tmp_path);view=await opened(broker,act);stored=broker.view(SCOPE,view['id']);reference=stored['document']
 broker.db.execute('DELETE FROM blobs WHERE id=?',(reference['$resource'],));broker.db.commit()
 status=await broker.read(SCOPE,view['id'],'status',revision=1)
 assert status['source']=='unavailable' and not status['canReconnect']
 assert broker.view(SCOPE,view['id'])['document']==reference
 assert len((tmp_path/'calls.jsonl').read_text().splitlines())==1
