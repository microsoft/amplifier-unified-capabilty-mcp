"""Private launcher stdio JSON-RPC. The embedding host supplies authenticated scope."""
import argparse,asyncio,json,os,sys
from pathlib import Path
from filelock import FileLock
from .broker import Broker,ACTIONS
MAX_FRAME=4_000_000
async def run(config,output):
 broker=Broker(config['dataDir'],server_config=config.get('server',{}));pending=set()
 def changed(value):
  output.write(json.dumps({'jsonrpc':'2.0','method':'broker/changed','params':value})+'\n');output.flush()
 broker.manager.on_change=changed
 def may_be_idle():
  output.write(json.dumps({'jsonrpc':'2.0','method':'broker/mayBeIdle','params':{}})+'\n');output.flush()
 broker.manager.on_may_be_idle=may_be_idle
 async def dispatch(row):
  identity=row.get('id')
  try:
   if row.get('jsonrpc')!='2.0' or not isinstance(row.get('params',{}),dict):raise ValueError('Invalid request.')
   p=row.get('params',{});method=row['method']
   if method=='initialize':result={'protocolVersion':1,'capabilities':{'actions':list(ACTIONS),'resources':True,'oauthCallback':True,'quiescence':{'version':1,'held':True,'serviceStop':{'version':1}}}}
   elif method=='quiescence/acquire':result=broker.quiescence.acquire(p['ownerId'],p['context'])
   elif method=='quiescence/inspect':result=broker.quiescence.inspect()
   elif method=='quiescence/release':result=broker.quiescence.release(p['ownerId'],p['context'],p['outcome'],p.get('proof'))
   elif method=='actions':result={name:{'description':value[0],'schema':value[1]} for name,value in ACTIONS.items()}
   elif method=='snapshot':result=broker.snapshot(p['scope'],offset=p.get('offset',0),limit=p.get('limit',25))
   elif method=='action':result=await broker.action(p['scope'],p['operation'],p.get('args',{}),p['commandId'],origin=p.get('origin','ui'))
   elif method=='read':result=await broker.read(p['scope'],p['id'],p['part'],revision=p.get('revision'),arguments=p.get('arguments'))
   elif method=='observation':result=await broker.observation(p['scope'],p['operation'],p['args'])
   elif method=='oauth/callback':
    from multidict import MultiDict
    result=await broker.oauth_callback(MultiDict(p['query']),p['origin'])
   else:raise ValueError('Unknown broker method.')
   response={'jsonrpc':'2.0','id':identity,'result':result}
  except Exception as exc:response={'jsonrpc':'2.0','id':identity,'error':{'code':-32000,'message':broker.manager._redact(str(exc))[:2000]}}
  text=json.dumps(response,ensure_ascii=False)
  if len(text.encode())>MAX_FRAME:text=json.dumps({'jsonrpc':'2.0','id':identity,'error':{'code':-32000,'message':'Broker response too large; request a smaller surface.'}})
  output.write(text+'\n');output.flush()
 reader=asyncio.StreamReader(limit=MAX_FRAME+1);protocol=asyncio.StreamReaderProtocol(reader)
 transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
 try:
  while line:=await reader.readline():
   if len(line)>MAX_FRAME:raise ValueError('Broker frame too large.')
   try:row=json.loads(line)
   except ValueError:continue
   if not isinstance(row,dict):continue
   if len(pending)>=32:await asyncio.wait(pending,return_when=asyncio.FIRST_COMPLETED)
   task=asyncio.create_task(dispatch(row));pending.add(task);task.add_done_callback(pending.discard)
 finally:
  transport.close()
  for task in pending:task.cancel()
  await asyncio.gather(*pending,return_exceptions=True)
  await broker.close()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args();config=json.loads(Path(args.config).read_text())
 root=Path(config['dataDir']).expanduser().resolve();root.mkdir(mode=0o700,parents=True,exist_ok=True);config['dataDir']=str(root)
 output=os.fdopen(os.dup(sys.stdout.fileno()),'w',buffering=1);os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
 with FileLock(str(root/'broker-owner.lock'),timeout=0):asyncio.run(run(config,output))
if __name__=='__main__':main()
