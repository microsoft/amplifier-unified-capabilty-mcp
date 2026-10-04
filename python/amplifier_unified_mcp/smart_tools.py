"""Managed Smart Tool installs and standards-based MCP connections.

Catalog metadata is inert. Only an explicit install or connect action executes
code. All MCP operations run in the task which owns their transport lifetime;
this is important because the SDK's AnyIO cancel scopes are task-local.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import urlsplit
import uuid

import jsonschema
import yaml

CATALOG_REPOSITORY = "https://github.com/microsoft/amplifier-smart-tools-catalog.git"
APP_MIME = "text/html;profile=mcp-app"
MAX_RESULT_BYTES = 2_000_000
MAX_HTML_BYTES = 2_000_000
MAX_TOOLS = 500
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXTRA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def configuration_key(config):
    """Bind an open surface to the exact executable configuration it reviewed."""
    fields = ("id", "command", "args", "env", "cwd")
    if config.get("transport", "stdio") != "stdio":
        fields += ("transport", "url", "headers", "auth")
    values = {key: config.get(key) for key in fields}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _bounded(value, limit=MAX_RESULT_BYTES):
    value = _json(value)
    if len(json.dumps(value, ensure_ascii=False).encode()) > limit:
        raise ValueError("The tool response is too large. Ask the tool for a smaller page or artifact reference.")
    return value


def _ui(tool):
    meta = tool.get("_meta", {})
    ui = meta.get("ui", {})
    return ui if isinstance(ui, dict) else {}


def _visible(tool, origin):
    visibility = _ui(tool).get("visibility", ["model", "app"])
    return ("app" if origin == "app" else "model") in visibility


def _repository(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        raise ValueError("Enter a credential-free HTTPS Git repository URL.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a credential-free HTTPS Git repository URL.")
    return value.rstrip("/")


def _relative(value):
    value = value or "."
    if not isinstance(value, str) or "\x00" in value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("The tool path must remain inside its repository.")
    return value


def _installation_identity(repository, commit, path, extras):
    return hashlib.sha256(json.dumps([repository, commit, path, sorted(extras)]).encode()).hexdigest()[:24]


def _install_environment():
    from mcp.client.stdio import get_default_environment
    environment = get_default_environment()
    for name in ("TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name in os.environ:
            environment[name] = os.environ[name]
    environment["GIT_TERMINAL_PROMPT"] = "0"
    # Discovery must not inherit checkout hooks or attribute filters configured
    # elsewhere on the machine. Authentication is supplied explicitly below.
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    if shutil.which("gh"):
        environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.https://github.com.helper", "GIT_CONFIG_VALUE_0": "!gh auth git-credential"})
    return environment


def _inside(root, relative):
    candidate = (root / _relative(relative)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("A tool file points outside its repository.")
    return candidate


def _manifest(text):
    if len(text.encode()) > 256_000 or not text.startswith("---\n"):
        raise ValueError("SMART_TOOL.md needs bounded YAML front matter.")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ValueError("SMART_TOOL.md has incomplete front matter.")
    data = yaml.safe_load(parts[0][4:])
    if not isinstance(data, dict) or data.get("smart_tool_format") != 1:
        raise ValueError("Only Smart Tool manifest format 1 is supported.")
    for key in ("name", "version", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"The Smart Tool manifest needs {key}.")
    # Requirements and instructions are displayed, never evaluated as commands.
    return _bounded(data, 32_000)


from .mcp_connection import Connection as _Connection
from .smart_tool_lifecycle import Lifecycle


from .smart_tool_updates import ManagedUpdates
from .smart_tool_installation import InstallProblem, python_project


class SmartToolsManager(Lifecycle, ManagedUpdates):
    def __init__(self, data_dir, *, server_config=None, _store_profile="manager"):
        from .store import open_store
        self.root = Path(data_dir).resolve() / "smart-tools"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = open_store(self.root / "broker.sqlite3", profile=_store_profile)
        self.db.executescript("CREATE INDEX IF NOT EXISTS operation_status ON smart_tool_operations(json_extract(value,'$.status')); CREATE INDEX IF NOT EXISTS operation_updated ON smart_tool_operations(json_extract(value,'$.updatedAt') DESC);")
        self.server_config = server_config or {}
        self.lock = asyncio.Lock()
        self.connections, self.schemas, self.connection_locks = {}, {}, {}
        self.install_lock, self.batch_lock = asyncio.Lock(), asyncio.Lock()
        self.closed = False
        self.background_io = set()
        self.revision = 0
        self.on_change = None
        self.on_may_be_idle = None
        self._state = {name: json.loads(row[0]) if (row := self.db.execute("SELECT value FROM records WHERE id=?", (name,)).fetchone()) else [] for name in ("servers", "catalog", "installations")}
        for server in self._state['servers']:
            server.update(status='disconnected', connectionState='disconnected', tools=[], loadedSchemas={}, catalogState='stale', account={'status':'unconfirmed','expected':copy.deepcopy(server['accountBinding']),'revision':server.get('accountRevision',0)} if server.get('accountBinding') else {'status':'unknown'},login={'id':server['id'],'phase':'idle'})
        for (value,) in self.db.execute("SELECT value FROM smart_tool_operations WHERE json_extract(value,'$.status') IN ('running','queued')").fetchall():
            operation=json.loads(value)
            operation.update(status='interrupted',outcome='unknown' if operation.get('requestState')=='dispatched' else 'not_sent',error='Broker restarted. Work was not replayed.',updatedAt=time.time())
            self.persist_operation(operation)
        self._state['operations']=[json.loads(row[0]) for row in reversed(self.db.execute("SELECT value FROM smart_tool_operations ORDER BY json_extract(value,'$.updatedAt') DESC LIMIT 50").fetchall())]
        from .mcp_oauth import Authorization
        from .mcp_account import Accounts
        self.oauth, self.accounts = Authorization(self), Accounts(self)
        self.db.commit()

    def persist_operation(self, operation):
        overview = self._overview(operation)
        self.db.execute("INSERT OR REPLACE INTO smart_tool_operations VALUES (?,?)",(operation['id'],json.dumps(overview)))
        operation.update(overview)

    def put_blob(self, value):
        text=json.dumps(value,ensure_ascii=False)
        identity=hashlib.sha256(text.encode()).hexdigest()
        self.db.execute("INSERT OR IGNORE INTO blobs VALUES (?,?)",(identity,text))
        return {'$resource':identity,'size':len(text.encode())}

    def get_blob(self, identity):
        row=self.db.execute('SELECT value FROM blobs WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('Saved broker resource unavailable; its reference is retained.')
        return json.loads(row[0])

    def _overview(self, operation):
        overview=copy.deepcopy(operation)
        for field in ('result','arguments'):
            value=overview.get(field)
            if field in overview.get('__broker_blob_fields',[]):continue
            if value is not None and len(json.dumps(value,ensure_ascii=False).encode())>16000:
                overview[field]={**self.put_blob(value),'summary':'Saved data; read the selected operation resource.'}
                overview.setdefault('__broker_blob_fields',[]).append(field)
        return overview

    def operation(self, identity):
        row=self.db.execute('SELECT value FROM smart_tool_operations WHERE id=?',(identity,)).fetchone()
        if not row:return None
        operation=json.loads(row[0])
        for field in ('result','arguments'):
            value=operation.get(field)
            if field in operation.get('__broker_blob_fields',[]):operation[field]=self.get_blob(value['$resource'])
        operation.pop('__broker_blob_fields',None)
        return operation

    async def inspect_operation(self, identity):
        operation = self.operation(identity)
        if operation is None:
            raise ValueError("This Smart Tool operation could not be found.")
        def inspect(state):
            state["inspectedOperation"] = self._overview(operation)
            return {"operationId": identity, "statePath": "/smartTools/inspectedOperation"}
        return await self._change(inspect,persist_metadata=False)

    def read_operation(self, args):
        """Read a retained receipt without creating work or moving a shared cursor."""
        from .agent_state import _preview, read_state
        identity = args['operationId']
        operation = self.operation(identity)
        if operation is None:
            raise ValueError('This Smart Tool operation could not be found or is no longer retained.')
        # Paging must not silently mix a running receipt with its terminal result.
        revision = hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()
        if args.get('operationRevision') not in (None, revision):
            raise ValueError('This operation changed. Read it again before continuing pages.')
        if args.get('format') == 'json' and args.get('path'):
            raise ValueError('JSON paging reads the whole operation; omit path.')
        if 'path' in args and args.get('format') != 'json':
            value = read_state(operation, {key: args[key] for key in ('path', 'offset', 'limit') if key in args}, reference_key='$operationPath')
            value.pop('revision', None)
        else:
            value = _preview(operation, '', 12000, reference_key='$operationPath')

        def long_reference(item):
            if isinstance(item, list):
                return any(long_reference(row) for row in item)
            if isinstance(item, dict):
                return any((key in {'$operationPath', 'path'} and isinstance(val, str) and len(val) > 4000)
                           or long_reference(val) for key, val in item.items())
            return False

        # Tool-owned keys can be arbitrarily long. Stream the original JSON if
        # their pointers/previews cannot fit the bounded structured interface.
        # This preserves every byte of string values without returning huge keys.
        text_page = 'path' in args and value.get('type') == 'string'
        oversized = not text_page and len(json.dumps(value, ensure_ascii=False)) > 26000
        json_mode = args.get('format') == 'json' or oversized or long_reference(value)
        if json_mode:
            serialized = json.dumps(operation, ensure_ascii=False)
            offset = args.get('offset', 0) if args.get('format') == 'json' else 0
            end = min(len(serialized), offset + min(args.get('limit', 12000), 12000))
            value = {'type': 'json', 'value': serialized[offset:end], 'offset': offset,
                     'total': len(serialized), 'nextOffset': end if end < len(serialized) else None}

        return {'operationId': identity, 'status': operation.get('status'),
                'operationRevision': revision, 'operation': value,
                'read': {'action': 'smartTools.readResult',
                         'args': {'operationId': identity, 'operationRevision': revision, **({'format': 'json'} if json_mode else {})},
                         'note': ('JSON pages contain the whole original operation. Continue with format=json and nextOffset; omit path.' if json_mode else
                                  'Paths are relative to this operation, not app state. Add path to read a preview in full; page with offset/limit and nextOffset. format=json pages the whole operation losslessly.')}}

    @property
    def state(self):
        return self._state

    def _server(self, identity):
        row = next((row for row in self.state["servers"] if row["id"] == identity), None)
        if row is None:
            raise ValueError("This Smart Tool connection is no longer registered.")
        return row

    async def _change(self, callback, *, defer_publish=False, persist_metadata=True):
        async with self.lock:
            result=callback(self.state)
            for name in ('servers','catalog','installations') if persist_metadata else ():
                value=self.state[name]
                if name=='servers':value=[{k:v for k,v in row.items() if k not in {'tools','loadedSchemas','capabilities','instructions','login','authorization'}} for row in value]
                encoded=json.dumps(value)
                self.db.execute("INSERT INTO records VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value WHERE value<>excluded.value",(name,encoded))
            self.db.commit()
            self.revision+=1
            if self.on_change:self.on_change({"revision":self.revision,"metadata":persist_metadata})
            return copy.deepcopy(result)

    def _redact(self, value):
        secrets = {
            os.environ[source] for server in self.state["servers"]
            for source in [*server.get("env", {}).values(), *server.get("headers", {}).values()]
            if source in os.environ and len(os.environ[source]) >= 4
        }
        secrets.update(self.oauth.secret_values())
        def clean(item):
            if isinstance(item, str):
                for secret in secrets:
                    item = item.replace(secret, "[redacted]")
                return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", item)
            if isinstance(item, dict):
                return {key: "[redacted]" if key.lower() in {"api_key", "apikey", "access_token", "refresh_token", "id_token", "authorization", "password", "client_secret"} else clean(val) for key, val in item.items()}
            if isinstance(item, list):
                return [clean(val) for val in item]
            return item
        return clean(value)

    async def command(self, action, args, command_id, origin="ui", *, defer_publish=False):
        args = copy.deepcopy(args)
        operation = {
            "id": command_id, "action": action, "origin": origin,
            "target": {key: args[key] for key in ("id", "name", "repository", "ref", "path", "sessionId", "uri", "names", "query") if key in args},
            "status": "running", "createdAt": time.time(), "updatedAt": time.time(),
        }
        if action in {"smartTools.call", "smartTools.resources", "smartTools.readResource"}:
            if action == "smartTools.call":
                operation["arguments"] = self._redact(copy.deepcopy(args.get("arguments", {})))
            server = next((row for row in self.state["servers"] if row["id"] == args.get("id")), None)
            if server is not None:
                operation["configuration"] = configuration_key(server)
                args.setdefault("_configuration", operation["configuration"])
        def add(state):
            # Do not lose an in-flight operation when trimming older results.
            previous = self.operation(command_id)
            if previous is not None and not (defer_publish and previous.get('action') == 'smartTools.appCall' and previous.get('status') == 'queued'):
                raise ValueError("This Smart Tool command already has a receipt.")
            state["operations"] = [row for row in state["operations"] if row['id'] != command_id and row.get("status") in {'running', 'queued'}] + [row for row in state["operations"] if row['id'] != command_id and row.get("status") not in {'running', 'queued'}][-49:]
            state["operations"].append(operation)
            self.persist_operation(operation)
        await self._change(add, defer_publish=defer_publish,persist_metadata=False)
        def finish(**values):
            operation.update(**values, updatedAt=time.time())
            self.persist_operation(operation)
        def dispatch():
            if args.get("_binding_guard"):args["_binding_guard"]()
            operation.update(requestState='dispatched',outcome='unknown',updatedAt=time.time())
            self.persist_operation(operation)
            self.db.commit()  # Durable before the transport can cause a tool effect.
        if action=='smartTools.call':args['_dispatch_guard']=dispatch
        try:
            result = self._redact(_bounded(await (self.install_batch(args, operation) if action == "smartTools.installBatch" else self.execute(action, args, origin=origin))))
            is_error = isinstance(result, dict) and result.get("isError") is True
            error = " ".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")[:1500] if is_error else None
            await self._change(lambda _: finish(status="failed" if is_error else "completed", outcome="confirmed", result=result, error=error), defer_publish=defer_publish,persist_metadata=False)
            return result
        except asyncio.CancelledError:
            for item in operation.get("items",[]):
                if item.get("status") in {"running","queued"}:item.update(status="interrupted",error="The request was interrupted. Work was not replayed.")
            await self._change(lambda _: finish(status="interrupted", outcome="unknown" if operation.get("requestState")=="dispatched" else "not_sent", error="The request was interrupted. Work was not replayed."), defer_publish=defer_publish,persist_metadata=False)
            raise
        except Exception as exc:
            from .smart_tool_lifecycle import ConnectionUnavailable
            if isinstance(exc, InstallProblem):
                operation['problem'] = exc.record(self._redact)
            if isinstance(exc, ConnectionUnavailable):
                operation.update(failureReason='connection_unavailable', requestState='not_sent')
            error = self._redact(str(exc))[:1500]
            await self._change(lambda _: finish(status="interrupted" if operation.get("requestState")=="dispatched" else "failed", outcome="unknown" if operation.get("requestState")=="dispatched" else "not_sent", error=error), defer_publish=defer_publish,persist_metadata=False)
            return None

    async def install_batch(self, args, operation):
        # Snapshot before waiting for the install lane, so even a queued batch
        # has durable per-item intent and can be inspected after restart.
        if self.closed:
            raise ValueError('Smart Tools are shutting down.')
        if args.get('retryOperationId'):
            previous=self.operation(args['retryOperationId'])
            if not previous or previous.get('action')!='smartTools.installBatch' or previous.get('status') in {'running','queued'}:
                raise ValueError('Choose a finished or interrupted installation batch.')
            items=[{**row,'status':'queued','error':None,'problem':None} for row in previous.get('items',[]) if row.get('status')!='completed']
            if not items:raise ValueError('This batch has no unfinished installations.')
        else:
            ids=args.get('ids',[])
            if not ids or len(ids)!=len(set(ids)):
                raise ValueError('Select one or more different catalog items.')
            catalog={row['id']:row for row in self.state['catalog']}
            if any(identity not in catalog for identity in ids):
                raise ValueError('The catalog changed. Refresh it before installing.')
            items=[{'id':identity,'name':catalog[identity]['name'],'source':{key:catalog[identity][key] for key in ('repository','ref','path') if key in catalog[identity]},'status':'queued'} for identity in ids]
            for item in items:
                if args.get('extrasById',{}).get(item['id']):item['source']['extras']=args['extrasById'][item['id']]
        def progress():
            operation.update(items=copy.deepcopy(items),updatedAt=time.time())
            self.persist_operation(operation)
        await self._change(lambda _:progress(),persist_metadata=False)
        async with self.batch_lock:
            for item in items:
                item['status']='running'
                await self._change(lambda _:progress(),persist_metadata=False)
                try:
                    result=await self.install(item['source'])
                    item.update(status='completed',installationId=result.get('id'))
                except asyncio.CancelledError:
                    for unfinished in items:
                        if unfinished['status'] in {'running','queued'}:unfinished.update(status='interrupted',error='Installation interrupted. Review before retrying.')
                    await self._change(lambda _:progress(),persist_metadata=False)
                    raise
                except Exception as exc:
                    item.update(status='failed',error=self._redact(str(exc))[:500])
                    if isinstance(exc, InstallProblem):
                        item['problem'] = exc.record(self._redact)
                await self._change(lambda _:progress(),persist_metadata=False)
            failed=sum(row['status']!='completed' for row in items)
            return {'completed':len(items)-failed,'failed':failed,'isError':bool(failed),'content':[{'type':'text','text':f"{len(items)-failed} installed; {failed} need attention."}]}

    async def execute(self, action, args, origin="ui"):
        if self.closed:
            raise ValueError("Smart Tools are shutting down.")
        name = action.removeprefix("smartTools.")
        if name == "authStart":
            return await self.oauth.start(args["id"], args.get("redirectOrigin"))
        if name == "authStatus":
            return self.oauth.status(args["id"])
        if name == "authCancel":
            return await self.oauth.cancel(args["id"])
        if name == "authForget":
            return await self.oauth.forget(args["id"])
        if name == "accountAccept":
            return await self.accounts.accept(args, origin)
        if name == "configure":
            return await self.configure(args)
        if name == "result":
            return await self.inspect_operation(args["operationId"])
        if name == "remove":
            await self.oauth.forget(args["id"])
            async with self.connection_locks.setdefault(args["id"], asyncio.Lock()):
                await self._disconnect(args["id"])
                return await self._change(lambda state: state.update(servers=[row for row in state["servers"] if row["id"] != args["id"]]))
        if name in {"connect", "reconnect"}:
            return await self.connect(args["id"], reconnect=name == "reconnect")
        if name == "discover":
            return await self.discover(args, origin)
        if name == "schemas":
            return await self.load_schemas(args, origin)
        if name == "uninstall":
            return await self.uninstall(args["id"])
        if name == "disconnect":
            await self.disconnect(args["id"])
            return {"id": args["id"], "status": "disconnected"}
        if name == "call":
            return await self.call_tool(args["id"], args["name"], args.get("arguments", {}), origin=origin, timeout_seconds=args.get("timeoutSeconds", 60), allowed_tools=args.get("_allowedTools"), expected_configuration=args.get("_configuration"), expected_catalog=args.get("catalogRevision"), dispatch_guard=args.get("_dispatch_guard"))
        if name in {"resources", "readResource"}:
            return await self.resource_request(args["id"], "read" if name == "readResource" else args.get("kind", "list"), uri=args.get("uri"), cursor=args.get("cursor"), expected_configuration=args.get("_configuration"))
        if name == "catalog":
            return await self.catalog()
        if name == "inspect":
            return await self.inspect(args)
        if name == "install":
            return await self.install(args)
        raise ValueError("Unknown Smart Tool action.")

    async def resource_request(self, identity, kind, *, uri=None, cursor=None, expected_configuration=None):
        """Forward bounded MCP resource requests, never resolve URIs in the host.

        The configured server owns resource authorization. A view gets only its
        saved server binding, with no authority to choose another connection.
        Large media must use tool-defined bounded resource chunks.
        """
        connection = await self._connection(identity)
        key = configuration_key(self._server(identity))
        if expected_configuration is not None and key != expected_configuration:
            raise ValueError("This tool's connection settings changed. Reopen its view before using it.")
        if "resources" not in self._server(identity).get("capabilities", {}):
            raise ValueError("This MCP server does not advertise resource access.")
        if kind == "read":
            if not isinstance(uri, str) or not uri or len(uri) > 4000 or any(ord(c) < 32 for c in uri):
                raise ValueError("Choose a resource URI supplied by this tool.")
            parsed = urlsplit(uri)
            if not parsed.scheme or parsed.username or parsed.password:
                raise ValueError("Resource URIs must be absolute and contain no credentials.")
            result = await connection.request("read_resource", uri, timeout=30)
        elif kind in {"list", "templates"}:
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4000):
                raise ValueError("The resource cursor is invalid.")
            result = await connection.request("list_resources" if kind == "list" else "list_resource_templates", cursor=cursor, timeout=30)
        else:
            raise ValueError("Unknown resource operation.")
        if self.connections.get(identity) is not connection or configuration_key(self._server(identity)) != key:
            raise ValueError("The resource connection changed while reading. Reopen its view.")
        return self._redact(_bounded(result))

    async def read_app(self, identity, uri):
        connection = await self._connection(identity)
        tools = self._server(identity)["tools"]
        if not isinstance(uri, str) or not uri.startswith("ui://") or not any(_ui(tool).get("resourceUri") == uri for tool in tools):
            raise ValueError("This view was not advertised by this MCP connection.")
        result = _bounded(await connection.request("read_resource", uri, timeout=30), MAX_HTML_BYTES + 32_000)
        contents = result.get("contents", [])
        content = next((item for item in contents if str(item.get("uri")) == uri), None)
        if content is None or content.get("mimeType", "").replace(" ", "").lower() != APP_MIME:
            raise ValueError("This view is not an MCP App HTML resource.")
        html = content.get("text")
        if not isinstance(html, str) or len(html.encode()) > MAX_HTML_BYTES:
            raise ValueError("MCP App HTML must be text smaller than 2 MB.")
        meta = content.get("_meta", {})
        ui = meta.get("ui", {}) if isinstance(meta, dict) else {}
        if not isinstance(ui, dict):
            raise ValueError("The MCP App resource metadata is invalid.")
        # The host decides which requested permissions it actually grants.
        return {"uri": uri, "html": self._redact(html), "csp": ui.get("csp", {}), "permissions": ui.get("permissions", {}), "meta": _bounded(meta, 32_000), "tools": [tool["name"] for tool in tools if _visible(tool, "app")]}

    def may_be_idle(self):
        try:
            if self.on_may_be_idle:self.on_may_be_idle()
        except Exception:pass  # Advisory wakeups never alter effect outcomes.

    async def background_thread(self, callback, *args):
        task = asyncio.create_task(asyncio.to_thread(callback, *args))
        self.background_io.add(task)
        def finished(done):
            self.background_io.discard(done)
            self.may_be_idle()
            if not done.cancelled():done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _run(self, argv, *, cwd=None, timeout=120, env=None, return_bytes=False):
        process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            detail = self._redact(err.decode(errors="replace"))[-1500:]
            raise ValueError(f"{Path(argv[0]).name} failed: {detail or 'The command did not complete.'}")
        if len(out) > 2_000_000:
            raise ValueError("The command returned too much metadata.")
        return out if return_bytes else out.decode(errors="replace").strip()

    async def _checkout(self, repository, ref=None):
        repository = _repository(repository)
        if ref is not None and (not isinstance(ref, str) or not ref or ref.startswith("-") or any(c.isspace() for c in ref) or len(ref) > 200):
            raise ValueError("Enter a Git branch, tag, or commit as the revision.")
        folder = self.root / "sources" / uuid.uuid4().hex
        folder.parent.mkdir(exist_ok=True, mode=0o700)
        env = _install_environment()
        try:
            await self._run(["git", "init", "--quiet", str(folder)], env=env)
            await self._run(["git", "remote", "add", "origin", repository], cwd=folder, env=env)
            await self._run(["git", "fetch", "--quiet", "--depth=1", "origin", ref or "HEAD"], cwd=folder, env=env)
            commit = await self._run(["git", "rev-parse", "FETCH_HEAD"], cwd=folder, env=env)
            await self._run(["git", "-c", f"core.hooksPath={os.devnull}", "checkout", "--quiet", "--detach", commit], cwd=folder, env=env)
            return folder, commit
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def _inspect_checkout(self, checkout, repository, commit, args):
        root = _inside(checkout, args.get("path"))
        descriptor_path = _inside(root, "smart-tool.json")
        if not descriptor_path.is_file() or descriptor_path.stat().st_size > 32_000:
            raise ValueError("This folder needs a smart-tool.json descriptor smaller than 32 KB.")
        descriptor = json.loads(descriptor_path.read_text())
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("manifest"), str):
            raise ValueError("smart-tool.json must identify a manifest file.")
        argv = descriptor.get("cli_argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
            raise ValueError("smart-tool.json must contain a nonempty cli_argv array.")
        manifest_path = _inside(root, descriptor["manifest"])
        if not manifest_path.is_file() or manifest_path.stat().st_size > 256_000:
            raise ValueError("The declared Smart Tool manifest is missing or too large.")
        manifest = _manifest(manifest_path.read_text())
        return {"repository": repository, "ref": args.get("ref") or "HEAD", "path": args.get("path") or ".", "commit": commit, "name": manifest["name"], "description": manifest["description"], "manifest": manifest, "descriptor": descriptor, **python_project(root), "status": "inspected"}

    async def inspect(self, args):
        repository = _repository(args["repository"])
        checkout, commit = await self._checkout(repository, args.get("ref"))
        try:
            return self._inspect_checkout(checkout, repository, commit, args)
        finally:
            shutil.rmtree(checkout, ignore_errors=True)

    async def setup(self, args):
        """Selected documentation from retained Git objects, never the worktree.

        This is guidance, not a readiness probe or permission to execute authored
        instructions. Legacy installed rows need no reinstall or receipt rewrite.
        """
        identity = args['id']
        row = next((copy.deepcopy(p) for p in self.state['installations'] if p['id'] == identity), None)
        if row is None or not re.fullmatch(r'[a-f0-9]{16,64}', identity):
            raise ValueError('This package is not installed.')
        commit = row.get('commit')
        if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
            raise ValueError('This installation has no exact source revision. Keep it and inspect its original source.')
        package_path = _relative(row.get('path'))
        extras = row.get('extras', [])
        if not isinstance(extras, list) or any(not isinstance(extra, str) or not EXTRA_NAME.fullmatch(extra) for extra in extras):
            raise ValueError('This installation has invalid package extras.')
        if _installation_identity(_repository(row.get('repository')), commit, package_path, extras) != identity:
            raise ValueError('This installation source differs from its retained identity.')
        source = self.root / 'installs' / identity / 'source'
        if source.is_symlink() or source.parent.is_symlink() or source.parent.parent.is_symlink():
            raise ValueError('This installation source is no longer app-owned.')
        root = _inside(source, package_path)
        if row.get('sourceDir') != str(root):
            raise ValueError('This installation source differs from its retained identity.')
        # Git's blob lookup neither executes the package nor follows worktree
        # symlinks. Disable machine configuration and avoid fetching any source.
        prefix = '' if package_path == '.' else package_path.rstrip('/') + '/'
        async def blob(path, maximum):
            relative = _relative(path)
            spec = commit + ':' + prefix + relative
            async def read():
                size = await self._run(['git', 'cat-file', '-s', spec], cwd=source,
                                       env=_install_environment(), timeout=10)
                if not size.isdecimal() or int(size) > maximum:
                    raise ValueError('This setup document exceeds the selected read limit.')
                return await self._run(['git', 'show', spec], cwd=source,
                                       env=_install_environment(), timeout=10, return_bytes=True)
            # Passive reads still hold their real subprocess lifetime through
            # cancellation, so maintenance cannot mistake them for idle.
            task = asyncio.create_task(read())
            self.background_io.add(task)
            def finished(done):
                self.background_io.discard(done)
                self.may_be_idle()
                if not done.cancelled():done.exception()
            task.add_done_callback(finished)
            value = await asyncio.shield(task)
            if len(value) > maximum:
                raise ValueError('This setup document exceeds the selected read limit.')
            return value.decode('utf-8')
        descriptor = json.loads(await blob('smart-tool.json', 32_000))
        if not isinstance(descriptor, dict) or descriptor != row.get('descriptor') or not isinstance(descriptor.get('manifest'), str):
            raise ValueError('The source descriptor differs from the installed receipt. Keep the receipt and inspect its source.')
        text = await blob(descriptor['manifest'], 256_000)
        if _manifest(text) != row.get('manifest'):
            raise ValueError('The source manifest differs from the installed receipt. Keep the receipt and inspect its source.')
        offset, limit = args.get('offset', 0), args.get('limit', 12000)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise ValueError('Setup document paging must be bounded.')
        declared = descriptor.get('cli_argv', [])
        smoke = descriptor.get('deterministic_smoke')
        connections = [{'id':p['id'], 'name':p['name'], 'status':p.get('connectionState', p.get('status'))}
                       for p in self.state['servers'] if p.get('installationId') == identity]
        result = self._redact(_bounded({'id':identity, 'source':{k:row.get(k) for k in ('repository','ref','path','commit')},
            'manifestPath':descriptor['manifest'], 'manifest':{'text':text[offset:offset+limit],
                'offset':offset,'total':len(text),'nextOffset':offset+limit if offset+limit<len(text) else None,
                'sha256':hashlib.sha256(text.encode()).hexdigest()},
            'cli':{'declaredArgv':declared,'binDirectory':row.get('binDir'), 'deterministicSmoke':smoke,
                   'verification':'not_run'}, 'requirements':row['manifest'].get('requires', []),
            'connections':connections, 'readiness':'not_assessed',
            'guidance':'Read the tool author’s instructions for CLI capabilities and optional MCP support. Installation alone does not prove prerequisites, model access, connected accounts or MCP readiness.'}, 128_000))
        result['manifest']['redacted'] = result['manifest']['text'] != text[offset:offset+limit]
        return result

    async def catalog(self):
        checkout, commit = await self._checkout(CATALOG_REPOSITORY, "main")
        try:
            from .catalog_snapshots import entry
            sources = []
            for source_path in (checkout / 'tools').glob('*/source.json'):
                sources.append(source_path)
                if len(sources) == 201:break
            truncated = len(sources) > 200
            rows, problems = [], []
            for source_path in sorted(sources[:200]):
                try:
                    rows.append(entry(source_path, checkout))
                except (KeyError, OSError, UnicodeError, ValueError, yaml.YAMLError):
                    # A bad snapshot must not disappear silently or present
                    # unverified author requirements as a valid catalog item.
                    problems.append({'id':source_path.parent.name,'code':'snapshot_unverified',
                                     'message':'This catalog snapshot could not be verified. Inspect its source pointer and refresh provenance.'})
            if rows:
                await self._change(lambda state: state.update(catalog=rows, catalogCommit=commit, catalogCheckedAt=time.time()))
            complete = bool(rows) and not problems and not truncated
            result = {'items':rows,'commit':commit,'problems':problems,'complete':complete,'truncated':truncated}
            if not complete:
                message = f'{len(rows)} verified catalog entries; {len(problems)} snapshots need review.'
                if truncated:message += ' More entries exceed this catalog limit.'
                if not rows:message += ' Previous inventory was retained.'
                result.update(isError=True, content=[{'type':'text','text':message}])
            return result
        finally:
            shutil.rmtree(checkout, ignore_errors=True)

    async def install(self, args):
        repository = _repository(args["repository"])
        extras = args.get("extras", [])
        if not isinstance(extras, list) or len(extras) > 20 or any(not isinstance(extra, str) or not EXTRA_NAME.fullmatch(extra) for extra in extras):
            raise ValueError("Package extras must be simple names such as mcp.")
        async with self.install_lock:
            try:
                checkout, commit = await self._checkout(repository, args.get("ref"))
            except (OSError, ValueError, TimeoutError) as exc:
                raise InstallProblem("source_unavailable", "Couldn't download this tool. Try again, or open the details for help.", str(exc)) from exc
            install_dir = None
            try:
                info = self._inspect_checkout(checkout, repository, commit, args)
                if not info["pythonSupported"]:
                    raise InstallProblem("invalid_package", "This tool needs manual installation. Open its setup instructions for help.", "Automatic installation supports Python packages with pyproject.toml.")
                identity = _installation_identity(repository, commit, info["path"], extras)
                install_dir = self.root / "installs" / identity
                previous = next((row for row in self.state["installations"] if row["id"] == identity), None)
                if previous and (install_dir / "venv" / "bin" / "python").exists():
                    return copy.deepcopy(previous)
                if install_dir.exists():
                    shutil.rmtree(install_dir)
                install_dir.mkdir(parents=True, mode=0o700)
                source = install_dir / "source"
                shutil.move(str(checkout), source)
                python = install_dir / "venv" / "bin" / "python"
                uv = shutil.which("uv")
                if not uv:
                    raise InstallProblem("installer_unavailable", "The tool installer isn't available. Ask the person who manages Amplifier Unified to enable it.", "uv is required to install Smart Tools.")
                environment = _install_environment()
                # Explicit constraints avoid the broker's working directory,
                # .python-version or an older Python on PATH choosing the runtime.
                # uv can obtain a compatible runtime without changing host Python.
                request = info["pythonRequires"] or sys.executable
                try:
                    await self._run([uv, "venv", "--no-project", "--no-config", "--python", request, str(install_dir / "venv")], timeout=300, env=environment, cwd=install_dir)
                except (OSError, ValueError, TimeoutError) as exc:
                    raise InstallProblem("runtime_unavailable", "Couldn't prepare the software this tool needs. Try again, or open the details for help.", str(exc)) from exc
                package = str(_inside(source, info["path"])) + ("[" + ",".join(extras) + "]" if extras else "")
                try:
                    await self._run([uv, "pip", "install", "--no-config", "--python", str(python), package], timeout=600, env=environment, cwd=install_dir)
                except (OSError, ValueError, TimeoutError) as exc:
                    raise InstallProblem("package_install_failed", "This tool couldn't be installed. Open the details for help or share them with its author.", str(exc)) from exc
                row = {**info, "id": identity, "status": "installed", "extras": extras, "installedAt": time.time(), "binDir": str(python.parent), "python": str(python), "sourceDir": str(_inside(source, info["path"])), "nextStep": "Read this tool's setup instructions for its CLI capabilities and optional MCP support. Installation does not start a server or configure model credentials."}
                if args.get('_update_from'): row['stagedFrom'] = args['_update_from']
                await self._change(lambda state: state.update(installations=[item for item in state["installations"] if item["id"] != identity] + [row]))
                return row
            except BaseException:
                if install_dir and not any(row["id"] == install_dir.name for row in self.state["installations"]):
                    shutil.rmtree(install_dir, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(checkout, ignore_errors=True)

    async def close(self):
        await asyncio.gather(*self.background_io, return_exceptions=True)
        await self.oauth.close()
        self.closed = True
        await asyncio.gather(*(connection.close() for connection in self.connections.values()), return_exceptions=True)
        self.connections.clear()
        self.schemas.clear()
