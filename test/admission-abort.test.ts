import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';import path from 'node:path';import {pathToFileURL} from 'node:url';
const {createMCPCapabilities}=await import(process.env.MCP_NODE_MODULE?pathToFileURL(path.resolve(process.env.MCP_NODE_MODULE)).href:'../src/index.js');
const c={fenceId:'abort-fence',commandId:'abort-command',purpose:'distribution-update' as const,instanceId:'host-original',dataScope:'owned-abort'};
const proof={...c,kind:'distribution-admission-abort' as const,verified:true as const,receiptId:'supervisor-abort'};
const receipt={ownerId:'mcp',status:'released',receiptId:'owner-abort',fenceId:c.fenceId,commandId:c.commandId,instanceId:c.instanceId,dataScope:c.dataScope};
const options=(request:any)=>({broker:{request,close:async()=>{}},inspectSession:async()=>({workingDirectory:'/owned'}),registerExternal:async()=>{throw Error('No artifact effect allowed')}});
const action=(owner:any)=>owner.action({version:1,channel:'host',topic:'connectors',operation:'smartTools.configure',args:{},commandId:'must-not-run'},{clientId:'fixture'});
const pause=()=>{let resolve!:(value?:any)=>void;const promise=new Promise<any>(done=>resolve=done);return {promise,resolve}};
const hello={protocolVersion:1,capabilities:{quiescence:{admissionAbort:{version:1}}}};

test('exact abort forwarding survives lost reply without Node gate reopening or effect retry',async()=>{
 let settled=0,lost=true,actions=0;const received:any[]=[];
 const owner=createMCPCapabilities(options(async(method:string,args:any)=>{
  if(method==='initialize')return hello;
  if(method==='quiescence/acquire')return {held:true,ownerId:'mcp',fenceId:c.fenceId};
  if(method==='quiescence/abortAdmission'){received.push(args);if(!settled)settled++;if(lost){lost=false;throw Error('lost abort reply')}return receipt}
  actions++;return {};
 }));
 try{
  const participant=owner.quiescenceParticipant();await participant.acquire(c);
  await assert.rejects(participant.abortAdmission({...c,proof}),/lost abort reply/);
  await assert.rejects(action(owner),/intake is closed/);assert.equal(actions,0);
  assert.deepEqual(await participant.abortAdmission({...c,proof}),receipt);
  assert.deepEqual(received[0],{ownerId:'mcp',context:c,proof});assert.equal(settled,1);
  assert.deepEqual(await participant.abortAdmission({...c,proof}),receipt);assert.equal(settled,1);
 }finally{await owner.close()}
});

for(const key of ['commandId','fenceId','instanceId','dataScope','kind','verified','purpose']){
 test(`changed proof ${key} never reaches the abort channel`,async()=>{
  let aborts=0;const owner=createMCPCapabilities(options(async(method:string)=>{if(method==='initialize')return hello;if(method==='quiescence/acquire')return {held:true,ownerId:'mcp',fenceId:c.fenceId};aborts++;return receipt}));
  try{const participant=owner.quiescenceParticipant();await participant.acquire(c);await assert.rejects(participant.abortAdmission({...c,proof:{...proof,[key]:'changed'}}),/proof/);assert.equal(aborts,0);await assert.rejects(action(owner),/intake is closed/)}finally{await owner.close()}
 });
}

test('missing support and misbound receipt retain Node intake; generic release proof cannot substitute',async()=>{
 for(const bad of [{...receipt,ownerId:'other'},{...receipt,instanceId:'new-host'},{...receipt,status:'unknown'},{...receipt,receiptId:''},{...receipt,extra:true},undefined]){
  const owner=createMCPCapabilities(options(async(method:string)=>method==='initialize'?hello:method==='quiescence/acquire'?{held:true,ownerId:'mcp',fenceId:c.fenceId}:bad));
  try{const p=owner.quiescenceParticipant();await p.acquire(c);await assert.rejects(p.abortAdmission({...c,proof}),/receipt/);await assert.rejects(action(owner),/intake is closed/);await assert.rejects(p.abortAdmission({...c,proof:{...c,verified:true,outcome:'unchanged',receiptId:'generic'}}),/proof/)}finally{await owner.close()}
 }
 const owner=createMCPCapabilities(options(async()=>({protocolVersion:1})));
 try{await assert.rejects(owner.quiescenceParticipant().abortAdmission({...c,proof}),/lacks/);await assert.rejects(action(owner),/intake is closed/)}finally{await owner.close()}
});

test('acquisition and abort pending work refuse concurrent abort, release and Smart Tool effects',async()=>{
 const acquired=pause(),acquireEntered=pause(),aborted=pause(),abortEntered=pause();let aborts=0,releases=0,effects=0;
 const owner=createMCPCapabilities(options(async(method:string)=>{
  if(method==='initialize')return hello;
  if(method==='quiescence/acquire'){acquireEntered.resolve();await acquired.promise;return {held:true,ownerId:'mcp',fenceId:c.fenceId}}
  if(method==='quiescence/abortAdmission'){aborts++;abortEntered.resolve();await aborted.promise;return receipt}
  if(method==='quiescence/release'){releases++;return {released:true}}effects++;return {};
 }));
 try{
  const p=owner.quiescenceParticipant(),acquire=p.acquire(c);await acquireEntered.promise;
  await assert.rejects(p.abortAdmission({...c,proof}),/pending/);assert.equal(aborts,0);acquired.resolve();await acquire;
  const abort=p.abortAdmission({...c,proof});await abortEntered.promise;
  await assert.rejects(p.abortAdmission({...c,proof}),/pending/);await assert.rejects(p.acquire(c),/pending/);
  await assert.rejects(p.reconcileRelease({...c,outcome:'unchanged',proof:{...c,verified:true,outcome:'unchanged',receiptId:'release'}}),/pending/);
  await assert.rejects(action(owner),/intake is closed/);assert.equal(effects,0);assert.equal(releases,0);
  aborted.resolve();assert.deepEqual(await abort,receipt);
 }finally{acquired.resolve();aborted.resolve();await owner.close()}
});

const python=process.env.MCP_BROKER_PYTHON??path.resolve('python/.venv/bin/python');
test('actual Python protocol lost abort reply and process replacement retain exact receipt',{skip:!existsSync(python)},async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'mcp-abort-')),config=path.join(dir,'broker.json'),script=path.join(dir,'lost-reply.py'),marker=path.join(dir,'lost-once');await writeFile(config,JSON.stringify({dataDir:path.join(dir,'owned')}));
 await writeFile(script,`import pathlib,runpy,sys\nfrom amplifier_unified_mcp.quiescence import Quiescence\noriginal=Quiescence.abort_admission\nmarker=pathlib.Path(sys.argv.pop(1))\ndef lost(self,*args):\n result=original(self,*args)\n if not marker.exists():\n  marker.write_text('settled original reply lost')\n  raise RuntimeError('owned lost abort reply')\n return result\nQuiescence.abort_admission=lost\nrunpy.run_module('amplifier_unified_mcp.server',run_name='__main__')\n`);
 const create=()=>createMCPCapabilities({...options(null),broker:{command:python,args:['-I','-B',script,marker,'--config',config]}});let owner=create();let original:any;
 try{
  const p=owner.quiescenceParticipant();await p.acquire(c);await assert.rejects(p.abortAdmission({...c,proof}),/owned lost abort reply/);await assert.rejects(action(owner),/intake is closed/);
  assert.equal(await readFile(marker,'utf8'),'settled original reply lost');original=await p.abortAdmission({...c,proof});assert.equal(original.status,'released');
  await owner.close();owner=create();assert.deepEqual(await owner.quiescenceParticipant().abortAdmission({...c,proof}),original);
  await assert.rejects(owner.quiescenceParticipant().abortAdmission({...c,proof:{...proof,receiptId:'changed'}}),/retained proof/);
  await assert.rejects(action(owner),/intake is closed/);
 }finally{await owner.close();await rm(dir,{recursive:true,force:true})}
});
