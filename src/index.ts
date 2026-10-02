import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'agent'|'ui';session?:string|{uri:string;workingDirectory?:string};workingDirectory?:string;}
export interface BrokerOptions {command:string;args?:string[];env?:Record<string,string>;cwd?:string;timeoutMs?:number;}
export interface Options {
 broker:BrokerOptions|{request:(method:string,args:Json)=>Promise<any>;close:()=>Promise<void>};
 /** Host authenticates the caller and returns authorized session metadata. */
 inspectSession:(uri:string,context:{clientId:string})=>Promise<{uri?:string;workingDirectory?:string}>;
 registerExternal:(context:{session:string;workingDirectory?:string},descriptor:Json)=>Promise<Json>;
 onInvalidate?:(topic:string,scope:string)=>void;
}
export class BrokerConnection {
 private child?:ChildProcessWithoutNullStreams;private pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();private id=0;private ready?:Promise<void>;private closed=false;
 constructor(private options:BrokerOptions,private changed?:()=>void){}
 private start(){
  if(this.closed)throw Error('MCP broker connection closed; uncertain work was not replayed.');
  if(!this.ready)this.ready=(async()=>{
   const child=this.child=spawn(this.options.command,this.options.args??[],{cwd:this.options.cwd,env:{...process.env,...this.options.env},stdio:'pipe'});
   child.stderr.on('data',()=>{}); // Owner writes diagnostics; never forward OAuth data to clients.
   child.on('error',()=>this.fail('MCP broker failed to start.'));child.on('exit',()=>this.fail('MCP broker exited; admitted outcomes may be unknown. No request was replayed.'));
   let frame=0;child.stdout.on('data',(data:Buffer)=>{for(const b of data){frame=b===10?0:frame+1;if(frame>4_000_000){this.fail('MCP broker frame exceeds 4 MB.');child.kill();break;}}});
   const lines=createInterface({input:child.stdout});lines.on('line',line=>{let row:Json;try{row=JSON.parse(line);}catch{this.fail('Invalid broker response.');child.kill();return;}if(row.method==='broker/changed'){this.changed?.();return;}const entry=this.pending.get(row.id);if(!entry)return;clearTimeout(entry.timer);this.pending.delete(row.id);row.error?entry.reject(Error(row.error.message)):entry.resolve(row.result);});
   const hello=await this.send('initialize',{});if(hello.protocolVersion!==1)throw Error('Unsupported MCP broker protocol.');
  })();return this.ready;
 }
 private fail(message:string){this.closed=true;for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error(message));}this.pending.clear();}
 private send(method:string,params:Json):Promise<any>{
  if(this.closed||!this.child)throw Error('MCP broker unavailable.');if(this.pending.size>=32)throw Error('MCP broker request capacity reached.');
  const id=++this.id,text=JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n';if(Buffer.byteLength(text)>4_000_000)throw Error('MCP broker request exceeds 4 MB.');
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.fail('MCP broker request timed out; outcome unknown and work was not replayed.');this.child?.kill();},this.options.timeoutMs??360000);this.pending.set(id,{resolve,reject,timer});this.child!.stdin.write(text,error=>{if(error)this.fail('MCP broker transport failed; outcome unknown.');});});
 }
 request=async(method:string,args:Json):Promise<any>=>{await this.start();return this.send(method,args);};
 async close(){this.fail('MCP broker connection closed.');if(!this.child||this.child.exitCode!==null)return;this.child.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.child?.kill();resolve();},3000);this.child!.once('exit',()=>{clearTimeout(timer);resolve();});});}
}
const NAMES=['list','catalog','inspect','install','installBatch','configure','reconnect','authStart','authStatus','accountAccept','authCancel','authForget','uninstall','discover','schemas','connect','disconnect','remove','call','readResult','result','resources','readResource','open','viewStatus','reconnectView','appCall','context'];
const scopeOf=(value:string)=>value==='ahp-root://' || value==='host'?'host':value;
function sessionOf(context:Context){return typeof context.session==='string'?context.session:context.session?.uri;}
/** Presentation records are bounded, while documents/results remain in the scoped owner. */
export class MCPCapabilities {
 readonly manifest={version:1,topics:{connectors:{uri:'amplifier-capability://mcp/connectors',version:1,watch:true}},actions:Object.fromEntries(NAMES.map(name=>['smartTools.'+name,{topic:'connectors',operation:'smartTools.'+name,method:'x-amplifier/capabilityAction'}]))};
 private broker:Options['broker']&{request?:(method:string,args:Json)=>Promise<any>};private sequence=0;private scopes=new Map<string,number>();private notification?:ReturnType<typeof setTimeout>;
 constructor(private options:Options){this.broker='request' in options.broker?options.broker:new BrokerConnection(options.broker,()=>this.changed());}
 private call(method:string,args:Json){return this.broker.request!(method,args);}
 private changed(){if(this.notification)return;this.notification=setTimeout(()=>{this.notification=undefined;for(const [scope,at] of this.scopes)if(Date.now()-at<300000)this.options.onInvalidate?.('connectors',scope);},20);}
 private async authorize(scope:string,context:Context){this.scopes.delete(scope);this.scopes.set(scope,Date.now());while(this.scopes.size>64)this.scopes.delete(this.scopes.keys().next().value!);if(scope==='host')return {};if(!scope.startsWith('ahp-session:/')||scope.length>8192)throw Error('AHP session scope required.');if(context.session&&sessionOf(context)!==scope)throw Error('Session scope does not match authenticated context.');return this.options.inspectSession(scope,{clientId:context.clientId});}
 private record(scope:string,data:Json){return {topic:'connectors',scope,revision:++this.sequence,data:{mcp:data}};}
 actionSchemas=async()=>this.call('actions',{});
 read=async(request:{uri:string;topic:string;scope:string;clientId:string})=>{if(request.topic!=='connectors')throw Error('Unknown MCP topic.');const scope=scopeOf(request.scope);await this.authorize(scope,{clientId:request.clientId});return this.record(scope,await this.call('snapshot',{scope}));};
 action=async(request:Json,context:Context)=>{
  const scope=scopeOf(request.channel);if(request.version!==1||request.topic!=='connectors'||!NAMES.includes(String(request.operation).replace(/^smartTools\./,''))||!String(request.operation).startsWith('smartTools.'))throw Error('MCP action is not advertised.');
  const trusted=await this.authorize(scope,context);const result=await this.call('action',{scope,operation:request.operation,args:request.args??{},commandId:request.commandId,origin:context.origin??'ui'});
  if(result.read)return {accepted:true,updates:request.operation==='smartTools.authStatus'?[this.record(scope,await this.call('snapshot',{scope}))]:[],invalidate:[],result:result.result};
  let descriptor=result.descriptor??result.receipt?.result?.descriptor;
  if(descriptor){descriptor=await this.options.registerExternal({session:scope,workingDirectory:trusted.workingDirectory},descriptor);result.artifact=descriptor;}
  this.options.onInvalidate?.('connectors',scope);
  return {accepted:true,updates:[this.record(scope,await this.call('snapshot',{scope}))],invalidate:[],result:result.receipt,_meta:{'amplifier.dev/operation':{id:request.commandId}},...(result.artifact?{artifact:result.artifact}:{})};
 };
 /** Standard AHP resource/read envelope; only the URI identifies the selected resource. */
 resourceRead=async(params:Json,context:{clientId:string})=>{
  if(params.channel!=='ahp-root://' || params.encoding&&params.encoding!=='utf-8')throw Error('MCP resources require root channel and UTF-8 encoding.');
  const url=new URL(params.uri);if(url.protocol!=='amplifier-mcp:'||url.username||url.password||url.hash)throw Error('Invalid MCP resource URI.');
  const scope=url.searchParams.get('session');if(!scope)throw Error('Resource URI needs an authenticated session selector.');await this.authorize(scope,context);
  const parts=url.pathname.split('/').filter(Boolean);let part:string,id:string;
  if(url.hostname==='operations'&&parts.length===1){[id]=parts;part='operation';}
  else if(url.hostname==='views'&&parts.length===2){[id,part]=parts;}else throw Error('Unknown MCP resource URI.');
  if(!/^[a-zA-Z0-9_-]{1,200}$/.test(id!))throw Error('Invalid MCP resource identity.');
  const rev=url.searchParams.get('revision');const revision=rev===null?undefined:Number(rev);if(part!=='operation'&&(!Number.isSafeInteger(revision)||revision!<1))throw Error('Immutable resource revision required.');
  const arguments_=Object.fromEntries(['kind','uri','cursor'].filter(k=>url.searchParams.has(k)).map(k=>[k,url.searchParams.get(k)]));
  const value=await this.call('read',{scope,id:id!,part:part!,revision,arguments:arguments_});
  return {data:part==='document'?value:JSON.stringify(value),encoding:'utf-8',contentType:part==='document'?'text/html;profile=mcp-app':'application/json'};
 };
 /** Only the configured host callback route may call this; it is never a capability action. */
 oauthCallback=async(query:Array<[string,string]>,origin:string)=>this.call('oauth/callback',{query,origin});
 /** Trusted product owner only. Qualification is not a tool's self-attestation. */
 qualifiedObservation=async(session:string,operation:'qualify'|'check'|'read',args:Json)=>{
  await this.authorize(session,{clientId:'',session});
  return this.call('observation',{scope:session,operation,args});
 };
 async close(){if(this.notification)clearTimeout(this.notification);await (this.broker as {close:()=>Promise<void>}).close();}
}
export function createMCPCapabilities(options:Options){return new MCPCapabilities(options);}
