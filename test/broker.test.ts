import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {existsSync} from 'node:fs';import {tmpdir} from 'node:os';import path from 'node:path';import {fileURLToPath} from 'node:url';
import {createMCPCapabilities,type Json} from '../src/index.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));const python=process.env.MCP_BROKER_PYTHON??path.join(root,'python/.venv/bin/python');
const scope='ahp-session:/node-fixture';
test('capability owner authenticates URI scope and registers metadata references only',async()=>{
 const calls:Json[]=[];let checked=0;const owner=createMCPCapabilities({broker:{request:async(method,args)=>{calls.push({method,args});if(method==='snapshot')return {servers:[]};if(method==='read')return '<p>scoped</p>';return {receipt:{id:args.commandId,status:'completed'}};},close:async()=>{}},inspectSession:async(uri)=>{assert.equal(uri,scope);checked++;return {workingDirectory:'/authorized'};},registerExternal:async()=>{throw Error('Unexpected');}});
 const read=await owner.read({uri:'amplifier-capability://mcp/connectors',topic:'connectors',scope,clientId:'c'});assert.deepEqual(read.data,{mcp:{servers:[]}});
 await assert.rejects(owner.resourceRead({channel:'ahp-root://',uri:'amplifier-mcp://views/a/document?revision=1'},{clientId:'c'}),/selector/);
 const html=await owner.resourceRead({channel:'ahp-root://',uri:'amplifier-mcp://views/a/document?session='+encodeURIComponent(scope)+'&revision=1'},{clientId:'c'});assert.equal(html.contentType,'text/html;profile=mcp-app');assert.equal(html.data,'<p>scoped</p>');assert.equal(checked,2);
 await assert.rejects(owner.action({version:1,channel:scope,topic:'connectors',operation:'smartTools.call',commandId:'x'},{clientId:'c',session:'ahp-session:/different'}),/authenticated/);
 assert.equal(calls.filter(c=>c.method==='action').length,0);await owner.close();
});
test('actual Node capability to Python stdio to independent MCP SDK server round trip',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'unified-mcp-test-'));const config=path.join(dir,'broker.json');await writeFile(config,JSON.stringify({dataDir:path.join(dir,'data')}));let descriptor:Json|undefined;
 const owner=createMCPCapabilities({broker:{command:python,args:['-m','amplifier_unified_mcp.server','--config',config],cwd:root},inspectSession:async(uri)=>{assert.equal(uri,scope);return {workingDirectory:dir};},registerExternal:async(context,value)=>{assert.equal(context.session,scope);assert.ok(Buffer.byteLength(JSON.stringify(value))<8192);assert.equal(value.content,undefined);descriptor=value;return value;}});
 let id=0;const context={clientId:'test',session:scope};const action=(operation:string,args:Json)=>owner.action({version:1,topic:'connectors',operation:'smartTools.'+operation,channel:scope,commandId:'command-'+(++id),args},context);
 try{
  const schemas=await owner.actionSchemas();assert.equal(schemas['smartTools.appCall'].schema.properties.expectedRevision.type,'integer');
  assert.deepEqual((await owner.read({uri:'amplifier-capability://mcp/connectors',topic:'connectors',scope,clientId:'test'})).data.mcp.servers,[]);
  const configured=await action('configure',{id:'fixture',name:'Fixture',command:python,args:[path.join(root,'python/tests/fixtures/server.py'),path.join(dir,'effects.jsonl'),path.join(dir,'mode')]});assert.equal(configured.result.status,'completed');
  assert.equal((await action('connect',{id:'fixture'})).result.status,'completed');
  const result=await action('call',{id:'fixture',name:'increment',arguments:{amount:4}});assert.equal(result.result.status,'completed');
  const opened=await action('open',{id:'fixture',tool:'increment',operationId:result.result.id});assert.equal(opened.result.status,'completed');assert.ok(descriptor);
  const html=await owner.resourceRead({channel:'ahp-root://',uri:descriptor!.resourceUri},{clientId:'test'});assert.match(html.data,/Saved fixture app/);
  const body=await owner.resourceRead({channel:'ahp-root://',uri:descriptor!.bodyUri},{clientId:'test'});assert.equal(JSON.parse(body.data).mcp.toolArguments.amount,4);
  const tools=await owner.resourceRead({channel:'ahp-root://',uri:descriptor!.resources.tools},{clientId:'test'});assert.ok(JSON.parse(tools.data).tools.find((t:Json)=>t.name==='increment'));
  const app=await action('appCall',{canvasId:descriptor!.id,expectedRevision:1,name:'increment',arguments:{amount:6}});assert.equal(app.result.status,'completed');assert.equal((await readFile(path.join(dir,'effects.jsonl'),'utf8')).trim().split('\n').length,2);
 }finally{await owner.close();await rm(dir,{recursive:true,force:true});}
});

for(const operation of ['install','inspect','call']){
 test(`inert ${operation} result descriptor never registers an artifact`,async()=>{
  const metadata={manifest:'SMART_TOOL.md',cli_argv:['example']};let registrations=0;
  const owner=createMCPCapabilities({broker:{request:async(method,args)=>method==='snapshot'?{}:{receipt:{id:args.commandId,status:'completed',result:{descriptor:metadata}}},close:async()=>{}},inspectSession:async()=>({workingDirectory:'/owned'}),registerExternal:async()=>{registrations++;throw Error('Invalid external artifact identity');}});
  try{const result=await owner.action({version:1,topic:'connectors',channel:scope,operation:'smartTools.'+operation,commandId:'inert-'+operation},{clientId:'test',session:scope});assert.equal(result.result.status,'completed');assert.deepEqual(result.result.result.descriptor,metadata);assert.equal(result.artifact,undefined);assert.equal(registrations,0);}finally{await owner.close();}
 });
}
for(const operation of ['open','reconnectView','context']){
 test(`retained ${operation} descriptor re-registers after acknowledgement loss`,async()=>{
  const descriptor={id:'saved-app',providerRevision:1,resourceUri:'amplifier-mcp://views/saved-app/document'};let registrations=0;
  const owner=createMCPCapabilities({broker:{request:async(method,args)=>method==='snapshot'?{}:{duplicate:true,receipt:{id:args.commandId,status:'completed',result:{descriptor}}},close:async()=>{}},inspectSession:async()=>({workingDirectory:'/owned'}),registerExternal:async(_context,value)=>{registrations++;assert.deepEqual(value,descriptor);return value;}});
  try{const result=await owner.action({version:1,topic:'connectors',channel:scope,operation:'smartTools.'+operation,commandId:'recover-'+operation},{clientId:'test',session:scope});assert.equal(registrations,1);assert.deepEqual(result.artifact,descriptor);}finally{await owner.close();}
 });
}
test('real Python inspect/install receipts keep package descriptors inert through the facade',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'unified-mcp-package-boundary-'));const config=path.join(dir,'broker.json');await writeFile(config,JSON.stringify({dataDir:path.join(dir,'data')}));
 const owner=createMCPCapabilities({broker:{command:python,args:[path.join(root,'python/tests/fixtures/install_broker.py'),'--config',config],cwd:root},inspectSession:async()=>({workingDirectory:dir}),registerExternal:async()=>{throw Error('Invalid external artifact identity');}});
 const source={repository:'https://example.invalid/boundary-fixture',ref:'a'.repeat(40),path:'.'};const context={clientId:'test',session:scope};
 const action=(operation:string,args:Json,commandId:string)=>owner.action({version:1,topic:'connectors',channel:scope,operation:'smartTools.'+operation,commandId,args},context);
 try{
  const inspected=await action('inspect',source,'inspect-package');assert.equal(inspected.result.status,'completed');assert.equal(inspected.artifact,undefined);assert.deepEqual(inspected.result.result.descriptor.cli_argv,['boundary-fixture']);
  const installed=await action('install',source,'install-package');assert.equal(installed.result.status,'completed',JSON.stringify(installed.result));assert.equal(installed.result.outcome,'confirmed');assert.equal(installed.artifact,undefined);assert.deepEqual(installed.result.result.descriptor.cli_argv,['boundary-fixture']);
  const {execFile}=await import('node:child_process');const {promisify}=await import('node:util');assert.equal((await promisify(execFile)(installed.result.result.python,['-c','import boundary_fixture; print(boundary_fixture.VALUE)'])).stdout.trim(),'installed');
  const again=await action('install',source,'install-package');assert.equal(again.result.status,'completed');assert.equal(again.result.id,installed.result.id);assert.deepEqual(again.result.result,installed.result.result);assert.equal(await readFile(path.join(dir,'data/smart-tools/fixture-checkouts'),'utf8'),'2');
  const retained=await action('readResult',{operationId:'install-package'},'passive-read');assert.equal(retained.result.operation.status,'completed');assert.equal(retained.result.operation.result.id,installed.result.result.id);
  const schemas=await owner.actionSchemas();assert.ok(schemas['smartTools.setup']);
  const setup=await action('setup',{id:installed.result.result.id},'setup-package');
  assert.equal(setup.artifact,undefined);assert.deepEqual(setup.updates,[]);
  assert.equal(setup.result.source.commit,installed.result.result.commit);
  assert.match(setup.result.manifest.text,/# Retained setup/);
  assert.equal(setup.result.readiness,'not_assessed');assert.equal(setup.result.cli.verification,'not_run');
  assert.equal(await readFile(path.join(dir,'data/smart-tools/fixture-checkouts'),'utf8'),'2');
 }finally{await owner.close();await rm(dir,{recursive:true,force:true});}
});

test('damaged authoritative receipt/configuration stores refuse actual broker startup without re-admission',{skip:!existsSync(python)},async t=>{
 const {execFileSync}=await import('node:child_process');
 for(const missing of [['records'],['commands'],['smart_tool_operations'],['commands','smart_tool_operations']])await t.test(missing.join('+'),async()=>{
  const dir=await mkdtemp(path.join(tmpdir(),'unified-mcp-damaged-'));const config=path.join(dir,'broker.json');await writeFile(config,JSON.stringify({dataDir:path.join(dir,'data')}));
  const script="import asyncio,json,sqlite3,sys;from pathlib import Path;from amplifier_unified_mcp.broker import Broker\nasync def seed():\n b=Broker(Path(sys.argv[1])/'data');r=await b.action('host','smartTools.configure',{'name':'Inert metadata','command':'/usr/bin/false'},'same-owned-command');assert r['receipt']['status']=='completed';await b.close()\nasyncio.run(seed())\np=Path(sys.argv[1])/'data/smart-tools/broker.sqlite3';db=sqlite3.connect(p)\nfor table in json.loads(sys.argv[2]):db.execute('DROP TABLE '+table)\ndb.commit();db.close()";
  execFileSync(python,['-B','-c',script,dir,JSON.stringify(missing)]);
  const database=path.join(dir,'data/smart-tools/broker.sqlite3');const original=await readFile(database);
  const owner=createMCPCapabilities({broker:{command:python,args:['-B','-m','amplifier_unified_mcp.server','--config',config],cwd:root},inspectSession:async()=>({}),registerExternal:async()=>{throw Error('No resource registration expected');}});
  try{
   await assert.rejects(owner.actionSchemas(),/broker exited|unavailable/i);
   await assert.rejects(owner.action({version:1,topic:'connectors',operation:'smartTools.configure',channel:'ahp-root://',commandId:'same-owned-command',args:{name:'Inert metadata',command:'/usr/bin/false'}},{clientId:'test'}),/broker|closed|unavailable/i);
   assert.deepEqual(await readFile(database),original);
  }finally{await owner.close();await rm(dir,{recursive:true,force:true});}
 });
});
