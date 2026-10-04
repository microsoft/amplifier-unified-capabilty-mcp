"""Existing authority loss refuses startup, never repairs receipts or inventory."""
import hashlib
import json
import sqlite3
import pytest
from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.smart_tools import SmartToolsManager
from amplifier_unified_mcp.store import BROKER_TABLES, StoreUnavailable

ARGS = {'name': 'Inert saved configuration', 'command': '/usr/bin/false', 'args': []}

def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode

async def saved(root):
    owner = Broker(root)
    receipt = await owner.action('host', 'smartTools.configure', ARGS, 'same-command')
    assert receipt['receipt']['status'] == 'completed'
    await owner.close()
    return root / 'smart-tools/broker.sqlite3', receipt

@pytest.mark.parametrize('missing', [[name] for name in BROKER_TABLES] + [['commands', 'smart_tool_operations']])
async def test_missing_authoritative_table_refuses_before_replay_or_normalization(tmp_path, missing):
    path, original = await saved(tmp_path)
    with sqlite3.connect(path) as db:
        # Authored unknown receipt data only; no peer/tool was launched.
        for table in ('commands', 'smart_tool_operations'):
            value = json.loads(db.execute('SELECT value FROM ' + table + " WHERE id='same-command'").fetchone()[0])
            value.update(status='running', outcome='unknown', requestState='dispatched')
            db.execute('UPDATE ' + table + " SET value=? WHERE id='same-command'", (json.dumps(value),))
        for table in missing: db.execute('DROP TABLE ' + table)
    before = fingerprint(path)
    with pytest.raises(StoreUnavailable, match='authoritative schema'): Broker(tmp_path)
    assert fingerprint(path) == before
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not (set(missing) & tables)
        for table in {'commands', 'smart_tool_operations'} - set(missing):
            assert json.loads(db.execute('SELECT value FROM ' + table).fetchone()[0])['status'] == 'running'
        if 'records' not in missing:
            assert len(json.loads(db.execute("SELECT value FROM records WHERE id='servers'").fetchone()[0])) == 1
    # A manager cannot downgrade surviving broker authority to manager-only.
    with pytest.raises(StoreUnavailable): SmartToolsManager(tmp_path)
    assert fingerprint(path) == before

@pytest.mark.parametrize('mutation', ['columns', 'key', 'view', 'check'])
async def test_incompatible_authoritative_schema_refuses_without_file_change(tmp_path, mutation):
    path, _ = await saved(tmp_path)
    with sqlite3.connect(path) as db:
        if mutation == 'check':
            db.execute('DROP TABLE owner_quiescence')
            db.execute('CREATE TABLE owner_quiescence(id INTEGER PRIMARY KEY,value TEXT NOT NULL)')
        else:
            db.execute('DROP TABLE commands')
            if mutation == 'columns': db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
            elif mutation == 'key': db.execute('CREATE TABLE commands(id TEXT,scope TEXT NOT NULL,hash TEXT NOT NULL,value TEXT NOT NULL)')
            else: db.execute('CREATE VIEW commands AS SELECT 1 AS id')
    before = fingerprint(path)
    with pytest.raises(StoreUnavailable, match='authoritative schema'): Broker(tmp_path)
    assert fingerprint(path) == before

@pytest.mark.parametrize('content', [b'', b'not a SQLite database'])
async def test_existing_empty_or_unreadable_store_is_not_initialization(tmp_path, content):
    folder = tmp_path / 'smart-tools'; folder.mkdir()
    path = folder / 'broker.sqlite3'; path.write_bytes(content); path.chmod(0o640)
    before = fingerprint(path)
    with pytest.raises(StoreUnavailable): Broker(tmp_path)
    assert fingerprint(path) == before

async def test_known_current_schema_restart_keeps_exact_duplicate_and_unknown_guard(tmp_path):
    path, original = await saved(tmp_path)
    with sqlite3.connect(path) as db:
        value = json.loads(db.execute("SELECT value FROM commands WHERE id='same-command'").fetchone()[0])
        value.update(status='interrupted', outcome='unknown', requestState='dispatched')
        for table in ('commands', 'smart_tool_operations'):
            db.execute('UPDATE ' + table + " SET value=? WHERE id='same-command'", (json.dumps(value),))
    owner = Broker(tmp_path)
    try:
        result = await owner.action('host', 'smartTools.configure', ARGS, 'same-command')
        assert result['duplicate'] and result['receipt'] == value
        assert len(owner.manager.state['servers']) == 1
        assert owner.command_receipt('host', 'same-command')['outcome'] == 'unknown'
        assert owner.db.execute("SELECT count(*) FROM commands WHERE json_extract(value,'$.outcome')='unknown'").fetchone()[0] == 1
    finally: await owner.close()

async def test_derived_indexes_rebuild_on_intact_authority(tmp_path):
    path, original = await saved(tmp_path)
    with sqlite3.connect(path) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchall():
            db.execute('DROP INDEX ' + name)
    owner = Broker(tmp_path)
    try:
        assert (await owner.action('host', 'smartTools.configure', ARGS, 'same-command'))['receipt'] == original['receipt']
        assert owner.db.execute("SELECT name FROM sqlite_master WHERE name='command_scope'").fetchone()
    finally: await owner.close()

async def test_legitimate_standalone_manager_profile_and_new_broker(tmp_path):
    manager = SmartToolsManager(tmp_path / 'standalone')
    await manager.configure(ARGS)
    await manager.close(); manager.db.close()
    manager = SmartToolsManager(tmp_path / 'standalone')
    assert len(manager.state['servers']) == 1
    await manager.close(); manager.db.close()
    # Profile promotion has no declared migration; do not infer one from a subset.
    with pytest.raises(StoreUnavailable): Broker(tmp_path / 'standalone')
    broker = Broker(tmp_path / 'new-broker')
    try: assert set(BROKER_TABLES) <= {r[0] for r in broker.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally: await broker.close()

@pytest.mark.parametrize('missing', ['records', 'commands', 'smart_tool_operations'])
async def test_crash_left_wal_authority_is_checked_read_only_without_checkpoint(tmp_path, missing):
    import subprocess
    import sys
    import amplifier_unified_mcp
    from pathlib import Path
    package_root = str(Path(amplifier_unified_mcp.__file__).parent.parent)
    script = """import asyncio,os,sys
sys.path.insert(0,sys.argv[3])
from amplifier_unified_mcp.broker import Broker
async def seed():
 b=Broker(sys.argv[1]);await b.action('host','smartTools.configure',{'command':'/usr/bin/false','name':'Inert WAL fixture'},'same-command')
 b.db.execute('PRAGMA wal_autocheckpoint=0');b.db.execute('DROP TABLE '+sys.argv[2]);b.db.commit()
 os._exit(0)
asyncio.run(seed())
"""
    subprocess.run([sys.executable, '-I', '-B', '-c', script, str(tmp_path), missing, package_root], check=True)
    path = tmp_path / 'smart-tools/broker.sqlite3'
    wal = path.with_name(path.name + '-wal')
    assert wal.exists() and wal.stat().st_size > 0
    before = (fingerprint(path), fingerprint(wal))
    with pytest.raises(StoreUnavailable, match=missing): Broker(tmp_path)
    assert (fingerprint(path), fingerprint(wal)) == before
    # A naive writable validator can checkpoint on close. Read-only admission
    # must preserve both authoritative files and never advertise capability.
