import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createMCPCapabilities,type Json} from '../src/index.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const python=process.env.MCP_BROKER_PYTHON??path.join(root,'python/.venv/bin/python');
const scope='ahp-session:/legacy-import';
const hash=(value:string)=>createHash('sha256').update(value).digest('hex');
const canonical=(value:any):string=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
const selection=()=>{
 const body={content:'<!doctype html><p>Original preserved HTML</p>'};
 const result={content:[{type:'text',text:'Retained original result'}]};
 const binding={serverId:'legacy-server',tool:'launch',allowedTools:['launch'],savedResult:{$resource:hash(canonical(result))}};
 return {format:'amplifier-unified-mcp-legacy-selection/v1',kind:'artifact',artifact:{id:'legacy-app',kind:'mcp-app',title:'Original App',sessionId:'native-old',revision:3,body:{$resource:hash(canonical(body))},mcpState:{$resource:hash(canonical(binding))}},body,binding,result};
};
test('installed broker import retains source, recovers receipt and fails closed under held intake',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'unified-mcp-import-'));
 const config=path.join(dir,'broker.json');await writeFile(config,JSON.stringify({dataDir:path.join(dir,'data')}));
 let registrations=0,refuse=false;
 const make=()=>createMCPCapabilities({broker:{command:python,args:['-m','amplifier_unified_mcp.server','--config',config],cwd:root},inspectSession:async(uri)=>{assert.equal(uri,scope);return {workingDirectory:dir};},registerExternal:async(_context,value)=>{registrations++;if(refuse)throw Error('Fixture compositor unavailable');return value;}});
 let owner=make();
 const source=JSON.stringify(selection());const args={sourceId:'offline-fixture',source,sourceSha256:hash(source),scope,legacySessionId:'native-old',commandId:'legacy-import'};
 try{
  const review=await owner.legacyReview(args,{clientId:'admin'});
  assert.equal(review.access,'read-only');
  const exact={...args,reviewDigest:review.reviewDigest};
  // A lost compositor acknowledgement cannot lose the already committed import.
  refuse=true;await assert.rejects(owner.legacyImport(exact,{clientId:'admin'}),/compositor/);
  assert.equal((await owner.legacyReceipt(args.commandId,scope,{clientId:'admin'})).outcome,'confirmed');
  refuse=false;const imported=await owner.legacyImport(exact,{clientId:'admin'});assert.equal(imported.duplicate,true);assert.equal(registrations,2);
  assert.equal((await owner.resourceRead({channel:'ahp-root://',uri:imported.descriptor.resourceUri},{clientId:'admin'})).data,selection().body.content);
  const status=JSON.parse((await owner.resourceRead({channel:'ahp-root://',uri:imported.descriptor.resources.status},{clientId:'admin'})).data);
  assert.equal(status.status,'imported_read_only');assert.equal(status.canReconnect,false);
  assert.equal((await owner.legacySource({commandId:args.commandId,scope,limit:10},{clientId:'admin'})).source,source.slice(0,10));
  const held=await owner.quiescenceParticipant().acquire({fenceId:'import-fence',commandId:'stop',purpose:'recovery',instanceId:'host-one',dataScope:'owned'});assert.ok(held);
  await assert.rejects(owner.legacyImport(exact,{clientId:'admin'}),/intake is closed/);
  assert.equal((await owner.legacyReceipt(args.commandId,scope,{clientId:'admin'})).status,'completed');
  await held!.release('unchanged',{kind:'admission-refused'});
  await owner.close();owner=make();
  assert.equal((await owner.legacyImport(exact,{clientId:'admin'})).duplicate,true);
  assert.equal((await owner.inspectQuiescence()).idleTransports,0);
 }finally{await owner.close();await rm(dir,{recursive:true,force:true});}
});
