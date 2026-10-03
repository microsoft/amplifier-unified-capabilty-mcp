"""Explicit selected offline migration; no source filesystem or runtime authority."""
import copy
import hashlib
import json
import re
import time

from .quiescence import token

MAX_SOURCE = 2_500_000
IDENTITY = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse(text):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('Duplicate source key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Nonfinite source number')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, RecursionError):
        raise ValueError('Selected source must be unambiguous finite JSON') from None


class LegacyImport:
    def __init__(self, broker):
        self.b = broker
        broker.db.execute('CREATE TABLE IF NOT EXISTS legacy_imports(command TEXT PRIMARY KEY,scope TEXT NOT NULL,binding TEXT NOT NULL UNIQUE,value TEXT NOT NULL)')
        broker.db.commit()

    def receipt(self, command, scope):
        token(command, 'import command')
        self.b.scope(scope)
        row = self.b.db.execute('SELECT value FROM legacy_imports WHERE command=? AND scope=?', (command, scope)).fetchone()
        if not row:
            raise ValueError('Import receipt unavailable in this scope')
        return json.loads(row[0])

    def source(self, command, scope, offset=0, limit=16000):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise ValueError('Bounded original source page required')
        receipt = self.receipt(command, scope)
        text = self.b.manager.get_blob(receipt['original']['$resource'])
        if not isinstance(text, str) or sha(text) != receipt['sourceSha256']:
            raise ValueError('Retained original source is unavailable or changed; nothing was replayed')
        end = min(len(text), offset + limit)
        return {'source': text[offset:end], 'offset': offset, 'nextOffset': end if end < len(text) else None,
                'sourceSha256': receipt['sourceSha256']}

    def plan(self, args):
        if not isinstance(args, dict) or set(args) - {'sourceId', 'source', 'sourceSha256', 'scope', 'legacySessionId', 'reviewDigest', 'commandId'}:
            raise ValueError('Exact selected import request required')
        token(args.get('sourceId'), 'source identity')
        text = args.get('source')
        if not isinstance(text, str) or len(text.encode()) > MAX_SOURCE:
            raise ValueError('Selected source exceeds bounded import size')
        source_digest = sha(text)
        if source_digest != args.get('sourceSha256'):
            raise ValueError('Selected source digest changed')
        value = parse(text)
        if not isinstance(value, dict) or value.get('format') != 'amplifier-unified-mcp-legacy-selection/v1':
            raise ValueError('Unsupported selected legacy format')
        kind = value.get('kind')
        scope = self.b.scope(args.get('scope'), session=kind == 'artifact')
        if kind == 'registration':
            if set(value) != {'format', 'kind', 'registration'} or scope != 'host' or args.get('legacySessionId') is not None:
                raise ValueError('Registration import belongs to host scope')
            original = value['registration']
            if not isinstance(original, dict):
                raise ValueError('Selected registration required')
            # The original record can retain passive diagnostics, never credentials.
            def credentials(record):
                if isinstance(record, dict):
                    return any(key in {'tokens', 'credentials', 'access_token', 'refresh_token', 'client_secret'} or credentials(item) for key,item in record.items())
                return isinstance(record, list) and any(credentials(item) for item in record)
            if credentials(original):
                raise ValueError('Credential migration is not supported')
            from .broker import ACTIONS
            config = {key: copy.deepcopy(original[key]) for key in ACTIONS['smartTools.configure'][1]['properties'] if key in original}
            import jsonschema
            try:
                jsonschema.validate(config, ACTIONS['smartTools.configure'][1])
            except jsonschema.ValidationError:
                raise ValueError('Selected registration fields are invalid') from None
            normalized = self.b.manager.registration(config)
            identity = original.get('id')
            if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
                raise ValueError('Original registration identity required')
            if normalized['installationId']:
                raise ValueError('Legacy package ownership cannot be transferred; retain original installation and review separately')
            target = {'kind': kind, 'id': identity, 'scope': scope, 'configuration': config}
        elif kind == 'artifact':
            if set(value) != {'format', 'kind', 'artifact', 'body', 'binding', 'result'}:
                raise ValueError('Select artifact, hydrated body, binding and result explicitly')
            original = value['artifact']
            if not isinstance(original, dict) or original.get('kind') != 'mcp-app':
                raise ValueError('Only original MCP App artifacts are supported')
            identity = original.get('id')
            if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
                raise ValueError('Original artifact identity required')
            legacy_session = token(args.get('legacySessionId'), 'legacy session identity')
            if original.get('sessionId') != legacy_session:
                raise ValueError('Exact legacy session mapping required')
            body = value['body']
            if not isinstance(body, dict) or not isinstance(body.get('content'), str) or len(body['content'].encode()) > 2_000_000:
                raise ValueError('Selected original HTML body required within 2 MB')
            if not isinstance(value['binding'], dict) or len(canonical(value['binding']).encode()) > 128_000:
                raise ValueError('Selected original binding required within 128 KB')
            if len(canonical(value['result']).encode()) > 2_000_000:
                raise ValueError('Selected original result exceeds 2 MB')
            def matches(reference, hydrated, name):
                if not isinstance(reference, dict) or reference.get('$resource') != sha(canonical(hydrated)):
                    raise ValueError('Selected ' + name + ' does not match its original content-addressed reference')
            matches(original.get('body'), body, 'body')
            if original.get('mcpState') is not None:
                matches(original['mcpState'], value['binding'], 'binding')
            elif value['binding']:
                raise ValueError('Binding has no original artifact reference')
            if value['binding'].get('savedResult') is not None:
                matches(value['binding']['savedResult'], value['result'], 'result')
            elif value['result'] is not None:
                raise ValueError('Result has no original saved reference')
            revision = original.get('revision', 1)
            if type(revision) is not int or not 1 <= revision <= 2**31-1:
                raise ValueError('Original immutable revision required')
            target = {'kind': kind, 'id': identity, 'revision': revision, 'scope': scope, 'legacySessionId': legacy_session}
        else:
            raise ValueError('Unsupported selected legacy kind')
        review = {'version': 1, 'sourceId': args['sourceId'], 'sourceSha256': source_digest, 'target': target,
                  'credentials': 'not-transferred', 'execution': 'not-replayed', 'access': 'disconnected' if kind == 'registration' else 'read-only'}
        return value, review, sha(canonical(review))

    def collision(self, review):
        target = review['target']
        if target['kind'] == 'registration':
            if any(row['id'] == target['id'] for row in self.b.manager.state['servers']):
                raise ValueError('Registration identity already exists; nothing was overwritten')
            if len(self.b.manager.state['servers']) >= 64:
                raise ValueError('Configured server limit reached')
        elif self.b.db.execute('SELECT 1 FROM views WHERE id=? LIMIT 1', (target['id'],)).fetchone():
            raise ValueError('Artifact identity already exists; nothing was overwritten')

    def review(self, args):
        _, review, binding = self.plan(args)
        prior = self.b.db.execute('SELECT value FROM legacy_imports WHERE binding=?', (binding,)).fetchone()
        if not prior:
            self.collision(review)
        return {**review, 'reviewDigest': binding, 'alreadyImported': bool(prior)}

    async def apply(self, args):
        async with self.b.quiescence.effect():
            return await self._apply(args)

    async def _apply(self, args):
        token(args.get('commandId'), 'import command')
        value, review, binding = self.plan(args)
        if args.get('reviewDigest') != binding:
            raise ValueError('Review the exact source and destination mapping before import')
        b = self.b
        async with b.manager.lock:
            prior = b.db.execute('SELECT scope,binding,value FROM legacy_imports WHERE command=?', (args['commandId'],)).fetchone()
            if prior:
                if prior[:2] != (args['scope'], binding):
                    raise ValueError('Import command belongs to a different reviewed source')
                return {**json.loads(prior[2]), 'duplicate': True}
            prior = b.db.execute('SELECT value FROM legacy_imports WHERE binding=?', (binding,)).fetchone()
            if prior:
                return {**json.loads(prior[0]), 'duplicate': True}
            self.collision(review)
            target = review['target']
            receipt = {'commandId': args['commandId'], 'status': 'completed', 'outcome': 'confirmed', **review,
                       'reviewDigest': binding, 'importedAt': time.time()}
            servers = None
            # No await or nested committing storage API inside this transaction.
            try:
                b.db.execute('BEGIN IMMEDIATE')
                receipt['original'] = b.manager.put_blob(args['source'])
                if target['kind'] == 'registration':
                    row = b.manager.registration(target['configuration'])
                    row['legacyImport'] = {'sourceId': args['sourceId'], 'reviewDigest': binding}
                    servers = [*b.manager.state['servers'], row]
                    stored = [{k:v for k,v in row.items() if k not in {'tools','loadedSchemas','capabilities','instructions','login','authorization'}} for row in servers]
                    b.db.execute("INSERT INTO records VALUES ('servers',?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(stored),))
                else:
                    view = {'id': target['id'], 'revision': target['revision'], 'sessionId': target['scope'],
                            'title': str(value['artifact'].get('title') or 'Imported MCP App')[:200],
                            'mcp': copy.deepcopy(value['binding']), 'document': b.manager.put_blob(value['body']['content']),
                            'result': b.manager.put_blob(value['result']), 'createdAt': time.time(),
                            'legacyImport': {'sourceId': args['sourceId'], 'reviewDigest': binding, 'legacySessionId': target['legacySessionId']}}
                    b.db.execute('INSERT INTO views VALUES (?,?,?,?)', (view['id'],view['revision'],view['sessionId'],json.dumps(view)))
                    receipt['descriptor'] = b.descriptor(view)
                b.db.execute('INSERT INTO legacy_imports VALUES (?,?,?,?)', (args['commandId'], args['scope'], binding, json.dumps(receipt)))
                b.db.commit()
            except BaseException:
                b.db.rollback()
                raise
            if servers is not None:
                b.manager.state['servers'] = servers
            b.manager.revision += 1
            if b.manager.on_change:
                b.manager.on_change({'revision': b.manager.revision, 'metadata': True})
            return receipt
