import asyncio
import copy
import json
import sys

import pytest

from amplifier_unified_mcp.smart_tool_installation import InstallProblem, python_project
from amplifier_unified_mcp.smart_tools import SmartToolsManager


@pytest.fixture
async def manager(tmp_path):
    instance = SmartToolsManager(tmp_path / "state")
    yield instance
    await instance.close()
    instance.db.close()


def tool(folder, metadata):
    folder.mkdir(parents=True)
    (folder / "smart-tool.json").write_text(json.dumps({"manifest": "SMART_TOOL.md", "cli_argv": [".venv/bin/example"]}))
    (folder / "SMART_TOOL.md").write_text("---\nsmart_tool_format: 1\nname: example\nversion: '1.0'\ndescription: Example task\n---\n")
    (folder / "pyproject.toml").write_text(metadata)


@pytest.mark.parametrize("metadata", [
    "[tool.uv.workspace]\nmembers=['packages/python']\n",
    "[project]\nrequires-python='/tmp/arbitrary/python'\n",
    "[project]\nrequires-python=['>=3.13']\n",
    "[project]\nrequires-python=''\n",
    "[project\n",
])
def test_nonpackage_or_invalid_python_constraint_is_inert(tmp_path, metadata):
    tool(tmp_path / "tool", metadata)
    with pytest.raises(InstallProblem) as failed:
        python_project(tmp_path / "tool")
    assert failed.value.code == "invalid_package"


def test_metadata_is_bounded_and_cannot_follow_outside_symlink(tmp_path):
    folder = tmp_path / "tool"
    folder.mkdir()
    outside = tmp_path / "private.toml"
    outside.write_text("[project]\nrequires-python='>=3.13'\n")
    (folder / "pyproject.toml").symlink_to(outside)
    with pytest.raises(InstallProblem, match="outside"):
        python_project(folder)
    (folder / "pyproject.toml").unlink()
    (folder / "pyproject.toml").write_text(" " * 256_001)
    with pytest.raises(InstallProblem, match="256 KB"):
        python_project(folder)


def test_legacy_backend_has_explicit_unknown_constraint(tmp_path):
    tool(tmp_path / "tool", "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n")
    assert python_project(tmp_path / "tool") == {"pythonSupported": True, "pythonRequires": None}
    assert python_project(tmp_path / "absent") == {"pythonSupported": False, "pythonRequires": None}


@pytest.mark.parametrize("requirement", [">=3.13,<3.14", ">=3.12,!=3.13.*", None])
async def test_install_selects_declared_range_independent_of_host_path(manager, tmp_path, monkeypatch, requirement):
    checkout = tmp_path / "checkout"
    tool(checkout, "[project]\nname='example'\nversion='1.0'\n" + (f"requires-python='{requirement}'\n" if requirement else ""))
    calls = []
    async def cloned(*_):
        return checkout, "a" * 40
    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return ""
    monkeypatch.setattr(manager, "_checkout", cloned)
    monkeypatch.setattr(manager, "_run", run)
    monkeypatch.setattr("amplifier_unified_mcp.smart_tools.shutil.which", lambda _: "/installer/uv")
    result = await manager.install({"repository": "https://example.com/tool", "extras": ["mcp"]})
    runtime, install = calls
    assert runtime[0][runtime[0].index("--python") + 1] == (requirement or sys.executable)
    assert "--no-project" in runtime[0] and "--no-config" in runtime[0]
    assert runtime[1]["cwd"] != checkout and runtime[1]["cwd"] == install[1]["cwd"]
    assert "--no-config" in install[0]
    assert result["pythonRequires"] == requirement
    assert install[0][install[0].index("--python") + 1] == result["python"]
    assert install[0][-1].endswith("[mcp]")
    assert not manager.connections and not manager.state["servers"]


@pytest.mark.parametrize("failed_stage,code", [("venv", "runtime_unavailable"), ("pip", "package_install_failed")])
async def test_failed_install_keeps_successes_and_persists_safe_diagnostic(manager, tmp_path, monkeypatch, failed_stage, code):
    existing = [{"id": "kept", "status": "installed", "account": "untouched"}]
    manager.state["installations"] = copy.deepcopy(existing)
    checkout = tmp_path / "checkout"
    tool(checkout, "[project]\nname='example'\nversion='1.0'\nrequires-python='>=3.13'\n")
    async def cloned(*_):
        return checkout, "b" * 40
    async def run(argv, **_):
        if argv[1] == failed_stage:
            raise ValueError("private diagnostic " * 150)
        return ""
    monkeypatch.setattr(manager, "_checkout", cloned)
    monkeypatch.setattr(manager, "_run", run)
    monkeypatch.setattr("amplifier_unified_mcp.smart_tools.shutil.which", lambda _: "/installer/uv")
    manager.state["catalog"] = [{"id": "tool", "name": "Example", "repository": "https://example.com/tool"}]
    await manager.command("smartTools.installBatch", {"ids": ["tool"]}, "batch")
    receipt = manager.operation("batch")
    problem = receipt["items"][0]["problem"]
    assert problem["code"] == code and "private diagnostic" not in problem["message"]
    assert len(problem["details"]) == 1500
    assert manager.state["installations"] == existing
    assert not list((manager.root / "installs").iterdir())
    assert not checkout.exists()


async def test_retry_only_unfinished_items_clears_stale_problem(manager, monkeypatch):
    previous = {"id": "old", "action": "smartTools.installBatch", "status": "failed", "updatedAt": 1,
                "items": [{"id": "kept", "status": "completed", "installationId": "installed"},
                          {"id": "retry", "status": "failed", "source": {"repository": "https://example.com/tool"},
                           "error": "old", "problem": {"code": "runtime_unavailable"}}]}
    manager.persist_operation(previous)
    manager.db.commit()
    calls = []
    async def install(args):
        calls.append(args)
        return {"id": "new"}
    monkeypatch.setattr(manager, "install", install)
    await manager.command("smartTools.installBatch", {"retryOperationId": "old"}, "retry")
    assert len(calls) == 1
    assert manager.operation("retry")["items"][0]["problem"] is None
    assert manager.operation("old") == previous


async def test_direct_source_failure_has_safe_problem_and_no_replay(manager, monkeypatch):
    calls = 0
    async def failed(*_):
        nonlocal calls
        calls += 1
        raise ValueError("Git could not resolve repository")
    monkeypatch.setattr(manager, "_checkout", failed)
    await manager.command("smartTools.install", {"repository": "https://example.com/tool"}, "direct")
    assert calls == 1
    assert manager.operation("direct")["problem"]["code"] == "source_unavailable"


async def test_cancellation_is_not_reclassified_as_retryable_failure(manager, monkeypatch):
    async def cancelled(*_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(manager, "_checkout", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await manager.command("smartTools.install", {"repository": "https://example.com/tool"}, "cancel")
    receipt = manager.operation("cancel")
    assert receipt["status"] == "interrupted" and "problem" not in receipt
