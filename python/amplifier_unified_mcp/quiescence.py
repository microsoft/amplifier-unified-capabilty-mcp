"""Private durable owner fence. Historical receipts are never execution evidence."""
from contextlib import asynccontextmanager
import json

READ_ACTIONS = frozenset({'smartTools.list', 'smartTools.readResult', 'smartTools.result', 'smartTools.viewStatus', 'smartTools.authStatus'})


def token(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid {name}.')
    return value


class Quiescence:
    def __init__(self, db, work, on_idle=lambda:None):
        self.db, self.work, self.active = db, work, 0
        self.on_idle = on_idle
        db.executescript('CREATE TABLE IF NOT EXISTS owner_quiescence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS owner_quiescence_receipts(fence TEXT PRIMARY KEY,value TEXT NOT NULL);')
        old = self.current()
        if old:
            old['state'] = 'unknown'
            self.save(old)
        db.commit()

    def current(self):
        row = self.db.execute('SELECT value FROM owner_quiescence WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self, value):
        self.db.execute('INSERT OR REPLACE INTO owner_quiescence VALUES(1,?)', (json.dumps(value),))
        self.db.execute('INSERT OR REPLACE INTO owner_quiescence_receipts VALUES(?,?)', (value['fenceId'], json.dumps(value)))
        self.db.commit()

    def inspect(self):
        return {'intakeClosed': bool(self.current()), 'fence': self.current(), 'activeRequests': self.active, **self.work()}

    @asynccontextmanager
    async def effect(self):
        if self.current():
            raise ValueError('MCP owner intake is closed for quiescence; no new effect was admitted.')
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self.on_idle()

    def acquire(self, owner_id, context):
        token(owner_id, 'owner identity')
        if not isinstance(context, dict):
            raise ValueError('Exact host context required.')
        value = {key: token(context.get(key), key) for key in ('fenceId', 'commandId', 'purpose', 'instanceId', 'dataScope')}
        if value['purpose'] not in {'distribution-update', 'recovery'}:
            raise ValueError('Unsupported quiescence purpose.')
        value.update(ownerId=owner_id, state='held')
        old = self.current()
        if old:
            if any(old.get(key) != value[key] for key in ('ownerId','fenceId','commandId','purpose','instanceId','dataScope')):
                raise ValueError('A different durable MCP fence requires reconciliation.')
            if old['state'] != 'held':
                raise ValueError('The MCP fence is unknown; inspect and reconcile, never reacquire.')
            return {'held': True, 'ownerId': owner_id, 'fenceId': value['fenceId']}
        if self.db.execute('SELECT 1 FROM owner_quiescence_receipts WHERE fence=?', (value['fenceId'],)).fetchone():
            raise ValueError('A released fence cannot be acquired again.')
        # Synchronous on the broker event loop: no effect can enter between count and fence.
        work = self.work()
        if self.active or work['gaps'] or any(work['counts'].values()):
            return {'held': False, 'executed': False, 'activeRequests': self.active, **work}
        self.save(value)
        return {'held': True, 'ownerId': owner_id, 'fenceId': value['fenceId']}

    def release(self, owner_id, context, outcome, proof):
        fence_id = token(context.get('fenceId'), 'fence identity')
        signature=json.dumps([outcome,proof],sort_keys=True,separators=(',',':'),allow_nan=False)
        old = self.current()
        if not old:
            row = self.db.execute('SELECT value FROM owner_quiescence_receipts WHERE fence=?', (fence_id,)).fetchone()
            prior = json.loads(row[0]) if row else None
            if prior and prior.get('state') == 'released' and prior.get('ownerId') == owner_id and all(prior.get(key)==context.get(key) for key in ('fenceId','commandId','purpose','instanceId','dataScope')) and prior.get('releaseSignature')==signature:
                return {'released': True, 'duplicate': True}
            raise ValueError('Exact retained MCP fence required.')
        if old['ownerId'] != owner_id or old['fenceId'] != fence_id or any(old.get(key) != context.get(key) for key in ('commandId','purpose','instanceId','dataScope')):
            raise ValueError('MCP fence context differs.')
        if outcome == 'unknown':
            old['state'] = 'unknown'; self.save(old)
            return {'released': False, 'intakeClosed': True}
        refused = proof == {'kind': 'admission-refused'} and old['state'] == 'held' and outcome == 'unchanged'
        verified = isinstance(proof, dict) and proof.get('verified') is True and proof.get('fenceId') == fence_id and proof.get('commandId') == old['commandId'] and proof.get('dataScope') == old['dataScope'] and proof.get('outcome') == outcome and outcome in {'ready','unchanged'}
        if verified:
            token(proof.get('receiptId'), 'authoritative receipt')
            token(proof.get('instanceId'), 'running instance')
            verified = (proof['instanceId'] == old['instanceId']) == (outcome == 'unchanged')
        if not refused and not verified:
            raise ValueError('Authoritative exact MCP release proof required.')
        old.update(state='released', outcome=outcome, releaseSignature=signature)
        self.db.execute('INSERT OR REPLACE INTO owner_quiescence_receipts VALUES(?,?)', (fence_id,json.dumps(old)))
        self.db.execute('DELETE FROM owner_quiescence WHERE id=1');self.db.commit()
        return {'released': True, 'intakeClosed': False}
