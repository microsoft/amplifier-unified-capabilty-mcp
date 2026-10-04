"""Admission for this owner's authoritative SQLite store, never data repair."""
from contextlib import closing
import os
import re
import sqlite3

MANAGER_TABLES = {
    'records': 'CREATE TABLE records (id TEXT PRIMARY KEY,value TEXT NOT NULL)',
    'blobs': 'CREATE TABLE blobs (id TEXT PRIMARY KEY,value TEXT NOT NULL)',
    'smart_tool_operations': 'CREATE TABLE smart_tool_operations (id TEXT PRIMARY KEY,value TEXT NOT NULL)',
}
BROKER_TABLES = {
    **MANAGER_TABLES,
    'commands': 'CREATE TABLE commands(id TEXT PRIMARY KEY,scope TEXT NOT NULL,hash TEXT NOT NULL,value TEXT NOT NULL)',
    'views': 'CREATE TABLE views(id TEXT,revision INTEGER,scope TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(id,revision))',
    'legacy_imports': 'CREATE TABLE legacy_imports(command TEXT PRIMARY KEY,scope TEXT NOT NULL,binding TEXT NOT NULL UNIQUE,value TEXT NOT NULL)',
    'owner_quiescence': 'CREATE TABLE owner_quiescence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)',
    'owner_quiescence_receipts': 'CREATE TABLE owner_quiescence_receipts(fence TEXT PRIMARY KEY,value TEXT NOT NULL)',
}


class StoreUnavailable(ValueError):
    """Existing authority cannot be established; nothing may be admitted."""


def normalized(sql):
    # Both declared profiles are the unchanged current schema. SQLite removes
    # IF NOT EXISTS when storing DDL; formatting carries no schema authority.
    return re.sub(r'\s+', '', sql or '').lower().replace('ifnotexists', '')


def validate_existing(path, profile):
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            names = tuple(BROKER_TABLES)
            slots = ','.join('?' for _ in names)
            tables = {name: (kind, sql) for kind, name, sql in db.execute(
                f"SELECT type,name,substr(sql,1,4097) FROM sqlite_master WHERE name IN ({slots}) AND type IN ('table','view')", names)}
            # A standalone manager may also read a full broker store. Never
            # downgrade a partially surviving broker schema to manager-only.
            full = profile == 'broker' or bool(set(tables) & (BROKER_TABLES.keys() - MANAGER_TABLES.keys()))
            expected = BROKER_TABLES if full else MANAGER_TABLES
            for name, sql in expected.items():
                actual = tables.get(name)
                if actual is None or actual[0] != 'table' or normalized(actual[1]) != normalized(sql):
                    raise StoreUnavailable('MCP store authoritative schema is missing or incompatible: ' + name + '.')
    except (sqlite3.Error, OSError) as exc:
        raise StoreUnavailable('MCP store is unavailable; preserve its database and receipts. Nothing was replayed.') from exc


def open_store(path, *, profile='manager'):
    if profile not in {'manager', 'broker'}:
        raise ValueError('Unknown MCP store profile.')
    # A missing main file does not establish a new data scope when any SQLite
    # crash evidence survives, including empty or dangling sidecar paths.
    if not os.path.lexists(path) and any(os.path.lexists(path.with_name(path.name + suffix))
                                       for suffix in ('-wal', '-shm', '-journal')):
        raise StoreUnavailable('Incomplete MCP store retains SQLite sidecars; preserve them. Nothing was initialized or replayed.')
    created = False
    try:
        # Exclusive creation distinguishes intentional new state from any
        # existing file, including an empty/incomplete prior initialization.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        validate_existing(path, profile)
    else:
        os.close(fd)
        created = True
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        if created:
            with db:
                db.execute('BEGIN')
                for sql in (BROKER_TABLES if profile == 'broker' else MANAGER_TABLES).values():
                    db.execute(sql)
        os.chmod(path, 0o600)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db
    except BaseException:
        db.close()
        raise
