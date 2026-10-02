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
