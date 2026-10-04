import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createMCPCapabilities,type Json} from '../src/index.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const python=process.env.MCP_BROKER_PYTHON??path.join(root,'python/.venv/bin/python');
const scope='ahp-session:/catalog-fixture';
test('actual broker retains named partial catalog failure and exact duplicate receipt',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'unified-catalog-'));const config=path.join(dir,'broker.json');
 await writeFile(config,JSON.stringify({dataDir:path.join(dir,'data')}));let registered=0;
 const owner=createMCPCapabilities({broker:{command:python,args:[path.join(root,'python/tests/fixtures/catalog_broker.py'),'--config',config],cwd:root},inspectSession:async()=>({workingDirectory:dir}),registerExternal:async()=>{registered++;throw Error('Unexpected artifact');}});
 const context={clientId:'test',session:scope};const request={version:1,topic:'connectors',operation:'smartTools.catalog',channel:scope,commandId:'catalog-refresh',args:{}};
 try{
  const first=await owner.action(request,context);assert.equal(first.result.status,'failed');assert.equal(first.result.outcome,'confirmed');
  assert.equal(first.result.result.complete,false);assert.equal(first.result.result.problems[0].id,'mismatch');
  assert.deepEqual(first.result.result.items.map((row:Json)=>row.id),['verified']);
  const data=(await owner.read({uri:'amplifier-capability://mcp/connectors',topic:'connectors',scope,clientId:'test'})).data.mcp;
  assert.deepEqual(data.catalog.map((row:Json)=>row.id),['verified']);assert.deepEqual(data.servers,[]);assert.deepEqual(data.installations,[]);
  assert.equal(data.catalog[0].snapshot.commit,'a'.repeat(40));assert.equal(data.catalog[0].ref,'main');
  const again=await owner.action(request,context);assert.deepEqual(again.result,first.result);
  assert.equal(await readFile(path.join(dir,'data/smart-tools/fixture-catalog-checkouts'),'utf8'),'1');assert.equal(registered,0);
 }finally{await owner.close();await rm(dir,{recursive:true,force:true});}
});
