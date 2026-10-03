"""Session-scoped MCP application owner. No application snapshots or private host objects."""
from __future__ import annotations
from .retention import selected, result, exists
import asyncio, copy, hashlib, json, time, uuid
from urllib.parse import urlencode
import jsonschema
from .smart_tools import SmartToolsManager, configuration_key, _visible, _bounded
from .definitions import definitions
from .quiescence import Quiescence, READ_ACTIONS

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def account(server):return {k:server['accountBinding'].get(k) for k in ('issuer','subject')} if server.get('accountBinding') else None
def binding_revision(binding):return digest({k:binding.get(k) for k in ('serverId','configuration','catalogRevision','resourceUri','tool','allowedTools','contractFingerprint','accountIdentity')})
def schema(properties=None,required=None):return {'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False}
def string(limit):return {'type':'string','minLength':1,'maxLength':limit}
ACTIONS=definitions(schema,string)
ACTIONS['smartTools.list']=('Read one metadata page without creating work.',schema({'collection':{'enum':['servers','catalog','installations','operations']},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50}},['collection']))
ACTIONS['smartTools.appCall'][1]['required']=['canvasId','expectedRevision','name','arguments']
ACTIONS['smartTools.context'][1]['required']=['canvasId','expectedRevision','context']

class Broker:
 def __init__(self,data_dir,*,server_config=None):
  self.manager=SmartToolsManager(data_dir,server_config=server_config)
  self.db=self.manager.db
  self.db.executescript('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,scope TEXT NOT NULL,hash TEXT NOT NULL,value TEXT NOT NULL);CREATE TABLE IF NOT EXISTS views(id TEXT,revision INTEGER,scope TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(id,revision));CREATE INDEX IF NOT EXISTS scoped_views ON views(scope,id,revision);')
  self.db.executescript("CREATE INDEX IF NOT EXISTS command_scope ON commands(scope); CREATE INDEX IF NOT EXISTS command_status ON commands(json_extract(value,'$.status'));")
  self.db.execute("UPDATE commands SET value=json_set(value,'$.status','interrupted','$.outcome','unknown','$.error','Broker restarted; work was not replayed.') WHERE json_extract(value,'$.status')='running'")
  self.db.execute("CREATE INDEX IF NOT EXISTS presentations ON views(scope,json_extract(value,'$.presentationKey'),revision)")
  self.db.execute("CREATE INDEX IF NOT EXISTS retention_commands ON commands(scope,json_extract(value,'$.status'),json_extract(value,'$.outcome'))");self.db.commit();self.locks={};self.active=0
  self.quiescence=Quiescence(self.db,self.quiescence_work,self.manager.may_be_idle)
 def retention_references(self,args):
  class Held:pass
  held=Held();held.fence=self.quiescence.current();sessions=selected(held,args)
  return result(sessions,lambda s:['mcp-unsettled'] if exists(self.db,"SELECT 1 FROM commands WHERE scope IN (?,'host') AND (json_extract(value,'$.status') IN ('pending','running','queued','unknown') OR json_extract(value,'$.outcome')='unknown') LIMIT 1",(s,)) else [])
 def scope(self,value,*,session=False):
  if not isinstance(value,str) or len(value)>8192 or not (value.startswith('ahp-session:/') or not session and value=='host'):raise ValueError('Trusted session scope is required.')
  return value
 def quiescence_work(self):
  live=[connection for connection in self.manager.connections.values() if not connection.task.done()]
  oauth=sum(1 for session in self.manager.oauth.sessions.values() if session.get('task') and not session['task'].done())
  return {'counts':{'commands':self.active,'oauthTasks':oauth,'backgroundIO':len(self.manager.background_io),'queuedSDKRequests':sum(connection.queue.qsize() for connection in live)},'gaps':['connected-peer-retirement-unverified'] if live else [],'idleTransports':len(live)}
 async def oauth_callback(self,query,origin):
  async with self.quiescence.effect():return await self.manager.oauth.callback(query,origin)
 async def observation(self,scope,operation,args):
  if operation=='check':return await self._observation(scope,operation,args)
  async with self.quiescence.effect():return await self._observation(scope,operation,args)
 async def _observation(self,scope,operation,args):
  """Trusted observation owner seam. No reconnect, sampling or publication."""
  from .observation_contract import qualify,check,validate_schema,validate_result,CONTRACT,bounded
  self.scope(scope,session=True)
  if operation=='qualify':return qualify(self.manager,args['descriptor'])
  descriptor=bounded(args['descriptor']);tool=check(self.manager,descriptor)
  if operation=='check':
   if args.get('configuration'):
    config=bounded(args['configuration']);validate_schema(config['target'],descriptor['targetSchema']);validate_schema(config['scope'],descriptor['scopeSchema'])
   return {'eligible':True,'id':descriptor['id']}
  if operation!='read':raise ValueError('Unknown qualified observation operation')
  watch=bounded(args['watch'],65536);request=watch['args']
  if descriptor['requestArgument'] in request:raise ValueError('Observation identity is host-owned')
  if watch['sessionId']!=scope or watch['observerId']!=descriptor['id']:raise ValueError('Observation scope does not match its saved owner')
  validate_schema(watch['target'],descriptor['targetSchema']);validate_schema(watch['scope'],descriptor['scopeSchema'])
  timeout=watch['readTimeoutSeconds']
  if type(timeout) is not int or not 1<=timeout<=30:raise ValueError('Observation read timeout must be bounded')
  occurrence=args['occurrenceId']
  if not isinstance(occurrence,str) or not 1<=len(occurrence)<=250:raise ValueError('Bounded occurrence identity required')
  # A read may have been queued before cancellation. Its product owner checks
  # authority again before accepting the result or admitting any explanation.
  deadline=time.monotonic()+timeout
  def guard():
   if time.monotonic()>deadline:raise ValueError('Queued observation read expired')
   check(self.manager,descriptor)
  envelope={'contract':CONTRACT,'watchId':watch['id'],'watchRevision':watch['revision'],'occurrenceId':occurrence,'target':watch['target'],'scope':watch['scope'],'cursor':watch.get('cursor')}
  result=await self.manager.call_tool(descriptor['connectionId'],descriptor['toolName'],{**request,descriptor['requestArgument']:envelope},origin='agent',timeout_seconds=timeout,allowed_tools=[descriptor['toolName']],expected_configuration=descriptor['configurationKey'],dispatch_guard=guard)
  if result.get('isError'):raise ValueError('Qualified observer reported a read failure')
  outcome=validate_result(result.get('structuredContent'),watch);tool=check(self.manager,descriptor);validate_schema(outcome,tool['outputSchema'])
  return outcome
 def command_receipt(self,scope,identity):
  row=self.db.execute('SELECT scope,value FROM commands WHERE id=?',(identity,)).fetchone()
  if not row or row[0]!=scope:raise ValueError('Operation is not accessible in this scope.')
  native=self.manager.operation(identity)
  value=native or json.loads(row[1])
  for field in ("result","arguments"):
   ref=value.get(field)
   if field in value.get('__broker_blob_fields',[]):value[field]=self.manager.get_blob(ref['$resource'])
  value.pop('__broker_blob_fields',None)
  return value
 def snapshot(self,scope,*,offset=0,limit=25):
  self.scope(scope)
  if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Use a bounded page.')
  result={'pages':{},'revision':self.manager.revision}
  for collection,budget,count in [('servers',300000,limit),('operations',160000,25),('catalog',160000,50),('installations',100000,50)]:
   page=self.list_page(scope,collection,offset=offset if collection=='servers' else 0,limit=count)
   selected=[];size=0
   for item in page['items']:
    length=len(json.dumps(item).encode())
    if size+length>budget:break
    selected.append(item);size+=length
   start=offset if collection=='servers' else 0
   result[collection]=selected
   result['pages'][collection]={'total':page['total'],'nextOffset':start+len(selected) if start+len(selected)<page['total'] else None}
  result.update(result['pages']['servers'])
  return self.manager._redact(_bounded(result,768000))
 def list_page(self,scope,collection,offset=0,limit=25):
  self.scope(scope)
  if collection not in {'servers','catalog','installations','operations'} or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('A bounded metadata page is required.')
  if collection=='operations':
   total=self.db.execute('SELECT count(*) FROM commands WHERE scope=?',(scope,)).fetchone()[0]
   rows=[]
   for identity,value in self.db.execute('SELECT id,value FROM commands WHERE scope=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(scope,limit,offset)):
    native=self.db.execute('SELECT value FROM smart_tool_operations WHERE id=?',(identity,)).fetchone();rows.append(json.loads(native[0] if native else value))
  else:
   source=self.manager.state[collection];total=len(source);rows=copy.deepcopy(source[offset:offset+limit])
   if collection=='servers':
    for row in rows:row['tools']=row.get('tools',[])[:25]
  items=[];size=0
  for row in rows:
   length=len(json.dumps(row).encode())
   if size+length>512000:break
   items.append(row);size+=length
  if not items and rows:raise ValueError('One metadata item exceeds the page bound; inspect its selected resource.')
  return {'collection':collection,'items':self.manager._redact(items),'total':total,'nextOffset':offset+len(items) if offset+len(items)<total else None,'revision':self.manager.revision}
 def contract(self,binding):
  tools={t['name']:t for t in self.manager.schemas.get(binding['serverId'],[])}
  launcher=tools.get(binding['tool']);names=sorted(set(binding['allowedTools']))
  if not launcher or launcher.get('_meta',{}).get('ui',{}).get('resourceUri')!=binding['resourceUri'] or any(name not in tools or not _visible(tools[name],'app') for name in names):return None
  value={'launcher':launcher,'tools':[tools[n] for n in names]}
  return {'fingerprint':digest(value),'tools':copy.deepcopy(value['tools'])}
 def view(self,scope,identity,revision=None):
  self.scope(scope,session=True)
  row=self.db.execute('SELECT value FROM views WHERE id=? AND scope=?'+(' AND revision=?' if revision is not None else ' ORDER BY revision DESC LIMIT 1'),(identity,scope,revision) if revision is not None else (identity,scope)).fetchone()
  if not row:raise ValueError('Saved tool view is not available in this session.')
  return json.loads(row[0])
 def uri(self,view,part):return 'amplifier-mcp://views/'+view['id']+'/'+part+'?'+urlencode({'session':view['sessionId'],'revision':view['revision']})
 def descriptor(self,view):
  b=view['mcp'];compact={k:b[k] for k in ('serverId','tool','operationId','contractFingerprint') if k in b}
  return {'id':view['id'],'sessionId':view['sessionId'],'kind':'mcp-app','title':view['title'],'providerRevision':view['revision'],'bodyUri':self.uri(view,'body'),'resourceUri':self.uri(view,'document'),'mcp':compact,'resources':{key:self.uri(view,key) for key in ('status','document','result','tools','resources')}}
 def save_view(self,view):
  self.db.execute('INSERT INTO views VALUES (?,?,?,?)',(view['id'],view['revision'],view['sessionId'],json.dumps(view)))
  self.db.commit();return self.descriptor(view)
 def status(self,view):
  b=view.get('mcp') or {};result={'canvasId':view['id'],'bindingRevision':binding_revision(b),'source':'available','canReconnect':False,'revision':view['revision']}
  def state(code,message,reconnect=False):return {**result,'status':code,'message':message,'canReconnect':reconnect}
  if not self.db.execute('SELECT 1 FROM blobs WHERE id=?',(view.get('document',{}).get('$resource'),)).fetchone():return {**state('source_unavailable','The original saved document is unavailable; its reference is retained and no work was replayed.'),'source':'unavailable'}
  if view['revision']!=self.view(view['sessionId'],view['id'])['revision']:return state('saved_version','Saved immutable view; select the current version for live access.')
  server=next((s for s in self.manager.state['servers'] if s['id']==b.get('serverId')),None)
  if not server or configuration_key(server)!=b.get('configuration'):return state('configuration_changed','The server configuration changed. Open a new view after reviewing it.')
  if server.get('account',{}).get('status') in {'changed','accepted'} or 'accountIdentity' in b and b['accountIdentity']!=account(server):return state('account_review_required','Review the connected account before using this view.')
  if server.get('status') not in {None,'connected'} or server.get('catalogState')!='current':return state('disconnected','Saved document available. Reconnect explicitly; work is never replayed.',True)
  if server.get('account',{}).get('status')=='unconfirmed':return state('account_review_required','The saved account cannot be confirmed.')
  current=self.contract(b)
  if not current:return state('contract_changed','The saved resource or granted tool contract changed.')
  if not b.get('contractFingerprint'):
   if account(server):return state('account_review_required','Legacy view has no saved account identity; open a new view.')
   return {**state('review_required','Review the saved actions before reconnecting.',True),'review':current}
  if current['fingerprint']!=b['contractFingerprint']:return state('contract_changed','The granted action schemas changed; open a new view.')
  if server['catalogRevision']!=b.get('catalogRevision'):return state('reconnect_required','Reconnect this view to the verified current contract.',True)
  return state('ready','Tool view connected',True)
 def bound(self,scope,identity,expected):
  if type(expected) is not int:raise ValueError('An immutable view revision is required.')
  view=self.view(scope,identity,expected);status=self.status(view)
  if status['status']!='ready':raise ValueError(status['message'])
  return view
 async def open_view(self,scope,args):
  self.scope(scope,session=True);manager=self.manager;server=manager._server(args['id'])
  key=configuration_key(server);catalog=server.get('catalogRevision')
  tool=next((t for t in manager.schemas.get(server['id'],[]) if t['name']==args['tool']),None)
  uri=tool.get('_meta',{}).get('ui',{}).get('resourceUri') if tool else None
  if not uri or not _visible(tool,'app'):raise ValueError('Select an advertised MCP App tool.')
  operation=self.command_receipt(scope,args['operationId']) if args.get('operationId') else None
  if operation and (operation.get('status')!='completed' or operation.get('configuration')!=key or operation.get('target',{}).get('id')!=server['id'] or operation.get('target',{}).get('name')!=tool['name']):raise ValueError('Only a completed call of this exact tool/configuration can initialize its view.')
  document=await manager.read_app(server['id'],uri)
  if configuration_key(manager._server(server['id']))!=key or manager._server(server['id']).get('catalogRevision')!=catalog:raise ValueError('The connection changed while opening its view.')
  binding={'serverId':server['id'],'configuration':key,'catalogRevision':catalog,'resourceUri':uri,'tool':tool['name'],'allowedTools':document['tools'],'accountIdentity':account(server),'toolArguments':operation.get('arguments',{}) if operation else {},'operationId':args.get('operationId'),'requestedCsp':document['csp'],'requestedPermissions':document['permissions'],'context':{}}
  contract=self.contract(binding)
  if not contract:raise ValueError('The granted tool contract is unavailable.')
  binding['contractFingerprint']=contract['fingerprint']
  view={'id':uuid.uuid4().hex,'revision':1,'sessionId':scope,'title':str(tool.get('title') or tool['name'])[:200],'mcp':binding,'document':manager.put_blob(document['html']),'result':manager.put_blob(operation.get('result') if operation else None),'createdAt':time.time()}
  if operation:
   value=(operation.get('result') or {}).get('_meta',{}).get('amplifier/presentationId')
   explicit=value if isinstance(value,str) and 0<len(value)<=200 else None
   namespace=[binding['configuration'],binding['accountIdentity']]
   key=digest([*namespace,binding['resourceUri'],['presentation',explicit]]) if explicit else digest([*namespace,binding['tool'],binding['resourceUri'],['operation',args['operationId']]])
   view['presentationKey']=key
   old=self.db.execute("SELECT value FROM views WHERE scope=? AND json_extract(value,'$.presentationKey')=? ORDER BY rowid DESC LIMIT 1",(scope,key)).fetchone()
   if old:
    prior=json.loads(old[0]);old_binding=prior['mcp'];now=self.contract(old_binding)
    compatible=all(old_binding.get(field)==binding.get(field) for field in ('serverId','configuration','accountIdentity','resourceUri','requestedCsp','requestedPermissions')) and set(old_binding['allowedTools'])==set(binding['allowedTools']) and now and now['fingerprint']==old_binding.get('contractFingerprint') and now['tools']==contract['tools']
    if compatible:
     latest=self.view(scope,prior['id']);view.update(id=prior['id'],revision=latest['revision']+1,createdAt=prior['createdAt'])
  return self.save_view(view)
 async def reconnect(self,scope,args):
  view=self.view(scope,args['canvasId']);before=self.status(view)
  if before['bindingRevision']!=args['expectedBindingRevision'] or not before['canReconnect']:raise ValueError('Inspect the current saved view before reconnecting.')
  b=view['mcp'];await self.manager.connect(b['serverId'],expected_configuration=b['configuration'])
  current=self.view(scope,view['id'])
  if current['revision']!=view['revision']:raise ValueError('View changed during reconnect.')
  status=self.status(current)
  if status['status']=='review_required' and args.get('reviewedContract')!=status['review']['fingerprint']:return status
  if status['status'] not in {'ready','reconnect_required','review_required'}:raise ValueError(status['message'])
  contract=self.contract(b);server=self.manager._server(b['serverId'])
  b.update(catalogRevision=server['catalogRevision'],contractFingerprint=contract['fingerprint'],accountIdentity=account(server))
  view['revision']+=1;descriptor=self.save_view(view)
  return {**self.status(view),'descriptor':descriptor}
 async def read(self,scope,identity,part,*,revision=None,arguments=None):
  if part!='resources':return await self._read(scope,identity,part,revision=revision,arguments=arguments)
  async with self.quiescence.effect():return await self._read(scope,identity,part,revision=revision,arguments=arguments)
 async def _read(self,scope,identity,part,*,revision=None,arguments=None):
  if part=='operation':return self.command_receipt(scope,identity)
  view=self.view(scope,identity,revision);args=arguments or {}
  if part=='status':return self.status(view)
  if part=='document':return self.manager.get_blob(view['document']['$resource'])
  if part=='result':return {'result':self.manager.get_blob(view['result']['$resource'])}
  if part=='body':
   b=copy.deepcopy(view['mcp']);b['savedResult']={'$resource':self.uri(view,'result')}
   return {'id':identity,'kind':'mcp-app','revision':view['revision'],'mcp':b,'resources':self.descriptor(view)['resources']}
  view=self.bound(scope,identity,revision);b=view['mcp']
  if part=='tools':return {'tools':self.contract(b)['tools']}
  if part=='resources':
   result=await self.manager.resource_request(b['serverId'],args.get('kind','list'),uri=args.get('uri'),cursor=args.get('cursor'),expected_configuration=b['configuration'])
   self.bound(scope,identity,revision)
   return result
  raise ValueError('Unknown MCP resource surface.')
 async def action(self,scope,operation,args,command_id,*,origin='ui'):
  if operation in READ_ACTIONS:return await self._action(scope,operation,args,command_id,origin=origin)
  async with self.quiescence.effect():return await self._action(scope,operation,args,command_id,origin=origin)
 async def _action(self,scope,operation,args,command_id,*,origin='ui'):
  self.scope(scope)
  if operation not in ACTIONS:raise ValueError('MCP action is not advertised.')
  if not isinstance(command_id,str) or not 1<=len(command_id)<=200:raise ValueError('Durable command identity is required.')
  jsonschema.validate(args,ACTIONS[operation][1])
  if len(json.dumps(args).encode())>300000:raise ValueError('Action arguments exceed the bounded request size.')
  if args.get('sessionId') not in (None,scope):raise ValueError('Session selector differs from trusted scope.')
  if operation in {'smartTools.open','smartTools.appCall','smartTools.context','smartTools.reconnectView','smartTools.viewStatus'}:self.scope(scope,session=True)
  if operation=='smartTools.list':return {'read':True,'result':self.list_page(scope,**args)}
  if operation in {'smartTools.readResult','smartTools.result','smartTools.viewStatus','smartTools.authStatus'}:
   if operation=='smartTools.authStatus':result=self.manager.oauth.status(args['id'])
   elif operation=='smartTools.viewStatus':result=self.status(self.view(scope,args['canvasId']))
   else:
    receipt=self.command_receipt(scope,args['operationId'])
    result=self.manager.read_operation(args) if operation=='smartTools.readResult' and self.manager.operation(args['operationId']) else receipt
   return {'read':True,'result':result}
  key=digest({'scope':scope,'operation':operation,'args':args,'origin':origin})
  prior=self.db.execute('SELECT scope,hash,value FROM commands WHERE id=?',(command_id,)).fetchone()
  if prior:
   if prior[:2]!=(scope,key):raise ValueError('Command identity already belongs to another request.')
   return {'receipt':self.command_receipt(scope,command_id),'duplicate':True}
  # Validate the current binding before admitting an app effect.
  view=None
  if operation in {'smartTools.appCall','smartTools.context'}:
   view=self.bound(scope,args['canvasId'],args['expectedRevision'])
   if operation=='smartTools.appCall' and args['name'] not in view['mcp']['allowedTools']:raise ValueError('This tool was not granted to the saved view.')
  if operation=='smartTools.configure' and not args.get('id') and len(self.manager.state['servers'])>=64:raise ValueError('Configured server limit reached.')
  if operation in {'smartTools.connect','smartTools.reconnect','smartTools.authStart'} and args.get('id') not in self.manager.connections and len(self.manager.connections)>=8:raise ValueError('Disconnect an unused MCP connection before opening another.')
  receipt={'id':command_id,'action':operation,'status':'running','createdAt':time.time(),'updatedAt':time.time(),'target':{k:v for k,v in args.items() if k in ('id','canvasId','name','operationId')}}
  self.db.execute('INSERT INTO commands VALUES (?,?,?,?)',(command_id,scope,key,json.dumps(receipt)));self.db.commit();self.active+=1
  descriptor=None
  try:
   if operation=='smartTools.open':descriptor=await self.open_view(scope,args);result={'canvasId':descriptor['id'],'revision':descriptor['providerRevision'],'resourceUri':descriptor['resourceUri'],'descriptor':descriptor}
   elif operation=='smartTools.reconnectView':result=await self.reconnect(scope,args);descriptor=result.get('descriptor')
   elif operation=='smartTools.viewStatus':result=self.status(self.view(scope,args['canvasId']))
   elif operation=='smartTools.context':
    if len(json.dumps(args['context']).encode())>16000:raise ValueError('View context exceeds 16 KB.')
    view['mcp']['context']={'untrusted':True,'value':copy.deepcopy(args['context']),'updatedAt':time.time()};view['revision']+=1;descriptor=self.save_view(view);result={'descriptor':descriptor,'recorded':True}
   elif operation=='smartTools.readResult':
    self.command_receipt(scope,args['operationId']);result=self.manager.read_operation(args) if self.manager.operation(args['operationId']) else self.command_receipt(scope,args['operationId'])
   elif operation=='smartTools.result':result=self.command_receipt(scope,args['operationId'])
   else:
    native_args=copy.deepcopy(args)
    if operation=='smartTools.appCall':
     b=view['mcp'];native_args={'id':b['serverId'],'name':args['name'],'arguments':args['arguments'],'sessionId':scope,'_configuration':b['configuration'],'_allowedTools':b['allowedTools'],'catalogRevision':b['catalogRevision'],'_binding_guard':lambda:self.bound(scope,args['canvasId'],args['expectedRevision'])}
    elif operation=='smartTools.call':native_args['sessionId']=scope
    await self.manager.command('smartTools.call' if operation=='smartTools.appCall' else operation,native_args,command_id,origin=('agent' if origin=='agent' else 'app') if operation=='smartTools.appCall' else origin)
    receipt=self.manager.operation(command_id)
    result=receipt.get('result')
   if receipt['status']=='running':receipt.update(status='completed',outcome='confirmed',result=result)
  except asyncio.CancelledError:
   receipt.update(status='interrupted',outcome='unknown',error='Broker request interrupted; work was not replayed.');raise
  except Exception as exc:receipt.update(status='failed',outcome='not_sent',error=self.manager._redact(str(exc))[:2000])
  finally:
   receipt['updatedAt']=time.time();self.db.execute('UPDATE commands SET value=? WHERE id=?',(json.dumps(self.manager._overview(receipt)),command_id));self.db.commit();self.active-=1
  return {'receipt':receipt,**({'descriptor':descriptor} if descriptor else {})}
 async def close(self):await self.manager.close();self.db.close()
