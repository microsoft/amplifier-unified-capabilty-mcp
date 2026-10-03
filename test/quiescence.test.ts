import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {existsSync} from 'node:fs';import {tmpdir} from 'node:os';import path from 'node:path';import {fileURLToPath} from 'node:url';
import {createMCPCapabilities,type Json} from '../src/index.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),python=process.env.MCP_BROKER_PYTHON??path.join(root,'python/.venv/bin/python');
const c={fenceId:'mcp-fence',commandId:'restart-command',purpose:'recovery' as const,instanceId:'host-one',dataScope:'fixture-private'};
const proof={verified:true as const,...c,outcome:'unchanged' as const,receiptId:'trusted-release'};
const pause=()=>{let release!:()=>void;const promise=new Promise<void>(resolve=>release=resolve);return {promise,release}};

test('Node post-effect artifact registration remains busy until its complete owner action settles',async()=>{
 const entered=pause(),finish=pause();let acquisitions=0;
 const owner=createMCPCapabilities({broker:{request:async(method,args)=>{if(method==='quiescence/acquire'){acquisitions++;return {held:true,ownerId:args.ownerId,fenceId:args.context.fenceId};}if(method==='snapshot')return {};return {descriptor:{id:'artifact'},receipt:{id:'command',status:'completed'}};},close:async()=>{}},inspectSession:async()=>({workingDirectory:'/owned'}),registerExternal:async(_context,descriptor)=>{entered.release();await finish.promise;return descriptor;}});
 try{
  const work=owner.action({version:1,channel:'ahp-session:/one',topic:'connectors',operation:'smartTools.open',commandId:'one',args:{}},{clientId:'c'});await entered.promise;
  assert.equal(await owner.quiescenceParticipant().acquire(c),null);assert.equal(acquisitions,0);finish.release();await work;assert.ok(await owner.quiescenceParticipant().acquire(c));
  await assert.rejects(owner.oauthCallback([], 'http://127.0.0.1:8941'),/intake is closed/);
 }finally{finish.release();await owner.close();}
});

test('actual broker held fence survives SIGKILL, preserves reads, and requires exact trusted release',{skip:!existsSync(python)},async()=>{
 const directory=await mkdtemp(path.join(tmpdir(),'mcp-gate-crash-')),config=path.join(directory,'broker.json'),pidfile=path.join(directory,'pid');await writeFile(config,JSON.stringify({dataDir:path.join(directory,'data')}));
 const launch="import os,pathlib,runpy,sys;pathlib.Path(sys.argv.pop(1)).write_text(str(os.getpid()));runpy.run_module('amplifier_unified_mcp.server',run_name='__main__')";
 const create=()=>createMCPCapabilities({broker:{command:python,args:['-c',launch,pidfile,'--config',config],cwd:root},inspectSession:async()=>({workingDirectory:directory}),registerExternal:async()=>{throw Error('Unexpected artifact');}});
 let owner=create();const action=(operation:string,args:Json)=>owner.action({version:1,channel:'host',topic:'connectors',operation:'smartTools.'+operation,commandId:operation,args},{clientId:'test'});
 try{
  const participant=owner.quiescenceParticipant(),lease=await participant.acquire(c);assert.ok(lease);
  const list=await action('list',{collection:'servers'});assert.deepEqual(list.result.items,[]);
  await assert.rejects(action('configure',{name:'Never start',command:'never'}),/intake is closed/);
  const pid=Number(await readFile(pidfile,'utf8'));process.kill(pid,'SIGKILL');await owner.close();owner=create();
  assert.equal((await owner.inspectQuiescence()).fence.state,'unknown');
  await assert.rejects(owner.quiescenceParticipant().acquire(c),/unknown/);
  await assert.rejects(action('configure',{name:'Never start',command:'never'}),/intake is closed/);
  assert.deepEqual((await action('list',{collection:'servers'})).result.items,[]);
  await assert.rejects(owner.quiescenceParticipant().reconcileRelease({...c,outcome:'unchanged',proof:{...proof,commandId:'wrong'}}),/proof/);
  await owner.quiescenceParticipant().reconcileRelease({...c,outcome:'unchanged',proof});
  assert.equal((await owner.inspectQuiescence()).intakeClosed,false);
  assert.equal((await action('configure',{id:'saved',name:'Saved metadata only',command:'never-spawn'})).result.status,'completed');
  assert.equal((await owner.inspectQuiescence()).idleTransports,0);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});

test('a private RPC timeout preserves the running owner and bounded late request instead of killing uncertain work',async()=>{
 const {BrokerConnection}=await import('../src/index.js');const directory=await mkdtemp(path.join(tmpdir(),'mcp-owner-timeout-')),log=path.join(directory,'alive');
 const script="const fs=require('node:fs'),readline=require('node:readline');setInterval(()=>fs.appendFileSync(process.argv[1],'x'),10);readline.createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line),reply=()=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,result:r.method==='initialize'?{protocolVersion:1}:{recorded:true}})+'\\n');if(r.method==='slow')setTimeout(reply,300);else reply();});process.stdin.on('end',()=>process.exit());";
 const connection=new BrokerConnection({command:process.execPath,args:['-e',script,log],timeoutMs:100});
 try{
  await assert.rejects(connection.request('slow',{}),/owner remains alive/);const before=(await readFile(log,'utf8')).length;await new Promise(r=>setTimeout(r,40));assert.ok((await readFile(log,'utf8')).length>before);
  assert.deepEqual(await connection.request('snapshot',{}),{recorded:true});await new Promise(r=>setTimeout(r,220));assert.deepEqual(await connection.request('snapshot',{}),{recorded:true});
 }finally{await connection.close();await rm(directory,{recursive:true,force:true});}
});
