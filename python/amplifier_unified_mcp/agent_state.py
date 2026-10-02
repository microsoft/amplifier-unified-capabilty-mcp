from pathlib import Path
import json,os,re,tempfile
from urllib.parse import urlsplit

def _pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _preview(value, path, budget=2500, depth=0, *, reference_key='$statePath'):
    if _size(value) <= budget:
        return value
    ref = {reference_key:path,'type':type(value).__name__,'size':len(value) if isinstance(value,(dict,list,str)) else None}
    if depth >= 4 or budget < 250:
        return ref
    if isinstance(value, str):
        return {**ref,'preview':value[:max(0,budget-180)]}
    if isinstance(value, list):
        count = min(5,len(value))
        return {**ref,'preview':[_preview(v,_pointer(path,i),max(100,(budget-250)//count),depth+1,reference_key=reference_key) for i,v in enumerate(value[:count])]}
    if isinstance(value, dict):
        keys=list(value)[:10]
        return {**ref,'preview':{k:_preview(value[k],_pointer(path,k),max(100,(budget-250)//len(keys)),depth+1,reference_key=reference_key) for k in keys}}
    return ref


def read_state(state, args, *, session_id=None, resolve=None, reference_key='$statePath'):
    if not isinstance(args,dict):
        raise ValueError('State arguments must be an object.')
    path=args.get('path')
    revision=args.get('revision')
    if revision is not None and revision != state.get('revision'):
        raise ValueError('App state changed. Read the current revision before continuing pages.')
    if path is None:
        return _preview(state,"",12000,reference_key=reference_key)
    if not isinstance(path,str) or (path and not path.startswith('/')):
        raise ValueError('path must be a JSON Pointer, for example /canvas or /sessions/0/messages.')
    value=state
    try:
        for token in path.split('/')[1:] if path else []:
            if resolve and isinstance(value,dict) and '$resource' in value:
                value=resolve(value['$resource'])
            token=token.replace('~1','/').replace('~0','~')
            value=value[int(token)] if isinstance(value,list) else value[token]
    except (KeyError,IndexError,TypeError,ValueError):
        raise ValueError('That state path does not exist.') from None
    if resolve and isinstance(value,dict) and '$resource' in value:
        value=resolve(value['$resource'])
    offset=args.get('offset',0);limit=args.get('limit',50)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=16000:
        raise ValueError('Use a nonnegative integer offset and a limit between 1 and 16000.')
    result={'path':path,'revision':state.get('revision'),'offset':offset,'nextOffset':None}
    if isinstance(value,str):
        end=min(len(value),offset+min(limit if 'limit' in args else 16000,16000))
        return {**result,'type':'string','value':value[offset:end],'total':len(value),'nextOffset':end if end<len(value) else None}
    if isinstance(value,(list,dict)):
        items=list(value.items()) if isinstance(value,dict) else list(enumerate(value))
        selected=items[offset:offset+min(limit,50)];rows=[];used=0
        for key,item in selected:
            preview=_preview(item,_pointer(path,key),2500,reference_key=reference_key)
            row={'key':key,'path':_pointer(path,key),'value':preview};size=_size(row)
            if rows and used+size>24000:break
            rows.append(row);used+=size
        end=offset+len(rows)
        return {**result,'type':'object' if isinstance(value,dict) else 'array','items':rows,'total':len(items),'nextOffset':end if end<len(items) else None}
    return {**result,'type':'scalar','value':value}
