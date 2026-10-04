import asyncio,copy,json,os,sys,sqlite3
from pathlib import Path
import pytest
from amplifier_unified_mcp.smart_tools import SmartToolsManager,_inside,_install_environment,_manifest,_repository,configuration_key
FIXTURE=Path(__file__).parent/'fixtures/mcp_unfamiliar_server.py'
@pytest.fixture
async def manager(tmp_path):
 instance=SmartToolsManager(tmp_path)
 yield instance
 await instance.close()
 instance.db.close()
async def register(manager, tmp_path, **overrides):
    return await manager.configure({
        "id": "board", "name": "Unfamiliar board", "command": sys.executable,
        "args": [str(FIXTURE), str(tmp_path / "board-value")], **overrides,
    })

async def test_visibility_and_schema_checked_before_the_tool_runs(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "view_only", {}, origin="agent")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "model_only", {}, origin="app")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "board_set", {"value": "no"}, origin="app", allowed_tools=["view_only"])
    with pytest.raises(ValueError, match="schema"):
        await manager.call_tool("board", "board_set", {"value": 7})
    assert not (tmp_path / "board-value").exists()
    assert (await manager.call_tool("board", "view_only", {}, origin="app"))["structuredContent"] == {"view": "compact"}

async def test_missing_environment_is_explained_without_starting_server(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_SMART_TEST_TOKEN", raising=False)
    await register(manager, tmp_path, env={"EXPLICIT_TOKEN": "MISSING_SMART_TEST_TOKEN"})
    with pytest.raises(ValueError, match="MISSING_SMART_TEST_TOKEN is not set"):
        await manager.connect("board")
    assert not manager.connections

async def test_unadvertised_and_wrong_mime_resources_are_rejected(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(ValueError, match="not advertised"):
        await manager.read_app("board", "ui://other-server/main")
    with pytest.raises(ValueError, match="not advertised"):
        await manager.read_app("board", "file:///etc/passwd")
    with pytest.raises(ValueError, match="HTML resource"):
        await manager.read_app("board", "ui://board/wrong-mime")

async def test_standard_resources_are_bounded_and_configuration_bound(manager, tmp_path):
    import base64
    await register(manager, tmp_path)
    connected = await manager.connect("board")
    key = configuration_key(connected)
    templates = await manager.resource_request("board", "templates", expected_configuration=key)
    assert any(row["uriTemplate"] == "board://retained/{identity}" for row in templates["resourceTemplates"])
    listed = await manager.resource_request("board", "list")
    assert any(row["uri"] == "ui://board/main" for row in listed["resources"])
    result = await manager.command("smartTools.readResource", {"id":"board", "uri":"board://retained/one"}, "media", origin="agent")
    assert base64.b64decode(result["contents"][0]["blob"]) == b"retained media"
    assert manager.operation("media")["configuration"] == key
    with pytest.raises(ValueError, match="too large"):
        await manager.resource_request("board", "read", uri="board://retained/large")
    for uri in ("relative/file", "https://user:secret@example.com/file", "board://bad\nuri"):
        with pytest.raises(ValueError, match="resource URI|Resource URIs"):
            await manager.resource_request("board", "read", uri=uri)
    with pytest.raises(ValueError, match="settings changed"):
        await manager.resource_request("board", "read", uri="board://retained/one", expected_configuration="old")

async def test_resource_read_rejects_connection_replaced_during_read(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    connection = manager.connections["board"]
    original = connection.request
    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        manager.state["servers"][0]["args"].append("replacement")
        return result
    connection.request = changed
    with pytest.raises(ValueError, match="changed while reading"):
        await manager.resource_request("board", "read", uri="board://retained/one")

async def test_disconnect_stops_transport_and_configuration_refreshes_schema(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    original = manager.connections["board"]
    await register(manager, tmp_path, name="Reconfigured")
    assert original.task.done()
    assert manager.state["servers"][0]["tools"] == []
    with pytest.raises(ValueError, match="Connect this tool"):
        await manager.call_tool("board", "board_set", {"value": "no"})

async def test_configuration_change_waits_for_connect_and_old_canvas_cannot_use_new_server(manager, tmp_path):
    await register(manager, tmp_path)
    key = configuration_key(manager.state["servers"][0])
    await asyncio.gather(manager.connect("board"), register(manager, tmp_path, args=[str(FIXTURE), str(tmp_path / "different-board")]))
    assert manager.state["servers"][0]["status"] == "disconnected"
    assert not manager.connections
    await manager.connect("board")
    with pytest.raises(ValueError, match="settings changed"):
        await manager.call_tool("board", "board_set", {"value": "wrong board"}, expected_configuration=key)
    assert not (tmp_path / "different-board").exists()

async def test_timeout_has_terminal_receipt_and_never_replays_work(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    await manager.command("smartTools.call", {"id": "board", "name": "slow", "timeoutSeconds": 0.05}, "slow")
    assert manager.state["operations"][-1]["status"] == "interrupted"
    assert manager.state["operations"][-1]["outcome"] == "unknown"
    assert "timed out" in manager.state["operations"][-1]["error"].lower()
    await manager.disconnect("board")
    before = (tmp_path / "board-value").stat().st_mtime_ns
    await manager.connect("board")
    assert (tmp_path / "board-value").stat().st_mtime_ns == before
    assert (tmp_path / "board-value").read_text() == "started"

async def test_command_duplicate_is_not_replayed(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    args = {"id": "board", "name": "board_set", "arguments": {"value": "once"}}
    await manager.command("smartTools.call", args, "same")
    before = (tmp_path / "board-value").stat().st_mtime_ns
    with pytest.raises(ValueError, match="receipt"):
        await manager.command("smartTools.call", args, "same")
    assert (tmp_path / "board-value").stat().st_mtime_ns == before

def test_untrusted_repository_and_manifest_paths_are_inert_and_bounded(tmp_path):
    for value in ("https://user:password@example.com/tool", "file:///tmp/tool", "https://example.com/tool#bad", "-u malicious"):
        with pytest.raises(ValueError):
            _repository(value)
    for value in ("../outside", "/absolute"):
        with pytest.raises(ValueError):
            _inside(tmp_path, value)
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        _inside(tmp_path, "link/file")
    document = "---\nsmart_tool_format: 1\nname: strange\nversion: '1.0'\ndescription: A domain tool\nrequires:\n  - install: rm -rf important-files\n---\nInstructions are data."
    assert _manifest(document)["requires"][0]["install"] == "rm -rf important-files"
    with pytest.raises(ValueError, match="format 1"):
        _manifest(document.replace("smart_tool_format: 1", "smart_tool_format: 2"))

def test_discovery_and_install_do_not_inherit_global_hooks_or_model_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/untrusted-global-config")
    environment = _install_environment()
    assert "OPENAI_API_KEY" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_CONFIG_SYSTEM"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"

async def test_inspection_does_not_install_or_run_descriptor(manager, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _write_tool(checkout)
    async def cloned(*args):
        return checkout, "a" * 40
    monkeypatch.setattr(manager, "_checkout", cloned)
    result = await manager.inspect({"repository": "https://example.com/strange.git"})
    assert result["status"] == "inspected" and result["pythonSupported"]
    assert result["commit"] == "a" * 40
    assert not manager.state["installations"] and not checkout.exists()

async def test_python_install_is_explicit_records_commit_and_does_not_guess_mcp_command(manager, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _write_tool(checkout)
    commands = []
    async def cloned(*args):
        return checkout, "b" * 40
    async def run(argv, **kwargs):
        commands.append(argv)
        return ""
    monkeypatch.setattr(manager, "_checkout", cloned)
    monkeypatch.setattr(manager, "_run", run)
    monkeypatch.setattr("amplifier_unified_mcp.smart_tools.shutil.which", lambda _: "/fake/uv")
    result = await manager.install({"repository": "https://example.com/strange.git", "extras": ["mcp"]})
    assert result["status"] == "installed" and result["commit"] == "b" * 40
    assert commands[0][1] == "venv" and commands[1][1:3] == ["pip", "install"]
    assert commands[1][-1].endswith("[mcp]")
    assert not manager.state["servers"] and not manager.connections
    assert "CLI capabilities and optional MCP support" in result["nextStep"]
    assert manager.state["installations"][0]["binDir"].endswith("/venv/bin")

async def test_waiting_batch_retains_source_and_extras_before_install_lane(manager,monkeypatch):
    manager.state['catalog']=[{'id':'tool','name':'Tool','repository':'https://example.com/tool','ref':'pinned'}]
    await manager.batch_lock.acquire()
    task=asyncio.create_task(manager.command('smartTools.installBatch',{'ids':['tool'],'extrasById':{'tool':['mcp']}},'waiting'))
    for _ in range(100):
        receipt=manager.operation('waiting')
        if receipt and receipt.get('items'):break
        await asyncio.sleep(.001)
    assert receipt['items'][0]['source']=={'repository':'https://example.com/tool','ref':'pinned','extras':['mcp']}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    manager.batch_lock.release()
    assert manager.operation('waiting')['items'][0]['status']=='interrupted'

def _write_tool(folder):
    folder.mkdir(parents=True)
    (folder / "smart-tool.json").write_text(json.dumps({"manifest": "SMART_TOOL.md", "cli_argv": [".venv/bin/strange"]}))
    (folder / "SMART_TOOL.md").write_text("---\nsmart_tool_format: 1\nname: strange\nversion: '1.0'\ndescription: A domain tool\n---\nRead-only instructions.")
    (folder / "pyproject.toml").write_text("[project]\nname='strange'\nversion='1.0'\n")
