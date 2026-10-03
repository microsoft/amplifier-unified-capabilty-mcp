import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';
const identity={installationId:'installation',dataScope:'owned',ownerId:'retained-platform',instanceId:'host-one',releaseDigest:'signed-release'};
const c={fenceId:'service-fence',commandId:'stop-service',purpose:'service-stop',instanceId:'host-one',dataScope:'owned',serviceIdentity:identity};
const p={verified:true,fenceId:c.fenceId,commandId:c.commandId,outcome:'ready',instanceId:'host-two',dataScope:c.dataScope,receiptId:'platform-settlement',kind:'service-lifecycle',serviceOutcome:'resumed',expected:identity,observed:{...identity,instanceId:'host-two'},resumeCommandId:'resume',exitReceiptId:'owned-exit',readyReceiptId:'authenticated-ready'};
import {createMCPCapabilities} from '../src/index.js';
const python=process.env.MCP_BROKER_PYTHON??resolve('python/.venv/bin/python');
test('installed Python service participant survives SIGKILL, rejects generic authority and reuses exact confirmed release',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'service-rpc-')),config=join(directory,'owner.json'),pidfile=join(directory,'pid');await writeFile(config,JSON.stringify({dataDir:join(directory,'data')}));
 const code="import os,pathlib,runpy,sys;pathlib.Path(sys.argv.pop(1)).write_text(str(os.getpid()));runpy.run_module('amplifier_unified_mcp.server',run_name='__main__')";
 const launcher={command:python,args:['-I','-c',code,pidfile,'--config',config]};const make=()=>createMCPCapabilities({broker:launcher,inspectSession:async()=>({workingDirectory:directory}),registerExternal:async()=>{throw Error('No resource writes')}});let owner=make();
 const action=(operation,args)=>owner.action({version:1,channel:'ahp-session:/one',topic:'connectors',operation,commandId:operation,args},{clientId:'fixture',session:'ahp-session:/one'});
 try{
  const participant=owner.quiescenceParticipant('mcp');assert.equal(participant.serviceStop.version,1);const lease=await participant.acquire(c);assert.ok(lease);
  await action('smartTools.list',{collection:'servers'});await assert.rejects(action('smartTools.configure',{id:'fixture',name:'Saved only',command:'never-spawn'}),/intake.*closed/);

  await assert.rejects(participant.reconcileRelease({...c,outcome:'unchanged',proof:{kind:'admission-refused'}}),/proof|identity/);
  const pid=Number(await readFile(pidfile,'utf8'));process.kill(pid,'SIGKILL');await new Promise(r=>setTimeout(r,40));await owner.close();owner=make();
  await assert.rejects(owner.quiescenceParticipant('mcp').reconcileRelease({...c,outcome:'ready',proof:{...p,kind:'distribution-update'}}),/proof/);
  await assert.rejects(action('smartTools.configure',{id:'fixture',name:'Saved only',command:'never-spawn'}),/intake.*closed/);await action('smartTools.list',{collection:'servers'});

  const exact={...c,outcome:'ready',proof:p};await owner.quiescenceParticipant('mcp').reconcileRelease(exact);await owner.close();owner=make();await owner.quiescenceParticipant('mcp').reconcileRelease(exact);
  await assert.rejects(owner.quiescenceParticipant('mcp').reconcileRelease({...exact,proof:{...p,readyReceiptId:'changed'}}),/proof|receipt|fence/i);
  await action('smartTools.configure',{id:'fixture',name:'Saved only',command:'never-spawn'});

 }finally{await owner.close();await rm(directory,{recursive:true,force:true})}
});
test('old broker is a known no-effect refusal before service acquisition',async()=>{
 let acquired=0;const owner=createMCPCapabilities({broker:{request:async method=>{if(method==='initialize')return {protocolVersion:1,capabilities:{quiescence:{version:1}}};if(method==='quiescence/acquire')acquired++;return {};},close:async()=>{}},inspectSession:async()=>({}),registerExternal:async()=>({})});try{assert.equal(await owner.quiescenceParticipant().acquire(c),null);assert.equal(acquired,0);}finally{await owner.close()}
});
