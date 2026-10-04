import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,chmod,access,rm} from 'node:fs/promises';import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';import path from 'node:path';import {pathToFileURL} from 'node:url';
const {createMCPCapabilities}=await import(process.env.MCP_NODE_MODULE?pathToFileURL(path.resolve(process.env.MCP_NODE_MODULE)).href:'../src/index.js');
const python=process.env.MCP_BROKER_PYTHON??path.resolve('python/.venv/bin/python');
test('actual broker fails before checkout without PATH, explicit installer readiness is passive and old failed receipt survives',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'mcp-installer-')),config=path.join(dir,'broker.json'),state=path.join(dir,'state'),uv=path.join(dir,'approved uv'),executed=path.join(dir,'must-not-execute');
 await writeFile(uv,`#!/bin/sh\ntouch "${executed}"\nexit 99\n`);await chmod(uv,0o700);
 await writeFile(config,JSON.stringify({dataDir:state}));
 const create=()=>createMCPCapabilities({broker:{command:python,args:['-I','-B','-m','amplifier_unified_mcp.server','--config',config],env:{PATH:path.join(dir,'absent')}},inspectSession:async()=>({workingDirectory:dir}),registerExternal:async()=>{throw Error('No artifacts allowed')}});
 let owner=create();const action=(operation:string,args:any,id:string)=>owner.action({version:1,channel:'host',topic:'connectors',operation:'smartTools.'+operation,args,commandId:id},{clientId:'fixture'});let original:any;
 try{
  assert.ok((await owner.actionSchemas())['smartTools.installerReadiness']);
  assert.equal(owner.quiescenceAccess['smartTools.installerReadiness'],'read');
  const missing=await action('installerReadiness',{},'missing-read');assert.equal(missing.result.status,'unavailable');
  original=(await action('install',{repository:'https://example.invalid/never-download'},'original-failed')).result;
  assert.equal(original.status,'failed');assert.equal(original.problem.code,'installer_unavailable');assert.doesNotMatch(original.problem.message,/enable/);
  await assert.rejects(access(path.join(state,'smart-tools','sources')));await assert.rejects(access(path.join(state,'smart-tools','installs')));
  await owner.close();await writeFile(config,JSON.stringify({dataDir:state,installer:{executable:uv}}));owner=create();
  const ready=await action('installerReadiness',{},'configured-read');assert.equal(ready.result.status,'available');assert.equal(ready.result.source,'configured');
  const held={fenceId:'held',commandId:'maintenance',purpose:'distribution-update' as const,instanceId:'fixture',dataScope:'fixture'};
  const p=owner.quiescenceParticipant();const lease=await p.acquire(held);assert.ok(lease);
  assert.deepEqual((await action('installerReadiness',{},'held-read')).result,ready.result);
  await lease.release('unchanged',{kind:'admission-refused'});
  assert.deepEqual((await action('install',{repository:'https://example.invalid/never-download'},'original-failed')).result,original);
  await assert.rejects(access(executed));await assert.rejects(access(path.join(state,'smart-tools','sources')));
 }finally{await owner.close();await rm(dir,{recursive:true,force:true})}
});
