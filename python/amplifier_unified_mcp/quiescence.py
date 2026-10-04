"""Private durable owner fence. Historical receipts are never execution evidence."""
from contextlib import asynccontextmanager
import json
import hashlib
from .service_lifecycle import service_identity, service_proof

READ_ACTIONS = frozenset({'smartTools.list', 'smartTools.setup', 'smartTools.readResult', 'smartTools.result', 'smartTools.viewStatus', 'smartTools.authStatus'})


def token(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid {name}.')
    return value


class Quiescence:
    def __init__(self, db, work, on_idle=lambda:None):
        self.db, self.work, self.active = db, work, 0
        self.on_idle = on_idle
        self.live_fence = None
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
        if value['purpose'] not in {'distribution-update', 'recovery', 'service-stop', 'retention-hide', 'managed-files-disposal'}:
            raise ValueError('Unsupported quiescence purpose.')
        if value['purpose']=='service-stop':
            value['serviceIdentity']=service_identity(context.get('serviceIdentity'))
            if any(value['serviceIdentity'][key]!=value[key] for key in ('instanceId','dataScope')):raise ValueError('Service identity differs from MCP fence')
        value.update(ownerId=owner_id, state='held')
        old = self.current()
        if old:
            if any(old.get(key) != value[key] for key in ('ownerId','fenceId','commandId','purpose','instanceId','dataScope')) or old.get('serviceIdentity')!=value.get('serviceIdentity'):
                raise ValueError('A different durable MCP fence requires reconciliation.')
            if old['state'] != 'held':
                raise ValueError('The MCP fence is unknown; inspect and reconcile, never reacquire.')
            return {'held': True, 'ownerId': owner_id, 'fenceId': value['fenceId']}
        if self.db.execute('SELECT 1 FROM owner_quiescence_receipts WHERE fence=?', (value['fenceId'],)).fetchone():
            raise ValueError('A released fence cannot be acquired again.')
        if value['purpose'] == 'distribution-update':
            value['admissionAttemptVersion'] = 1
            self.db.execute('INSERT INTO owner_quiescence_receipts VALUES(?,?)',
                            (value['fenceId'], json.dumps({**value, 'state': 'attempted'})))
            self.db.commit()
        # Synchronous on the broker event loop: no effect can enter between count and fence.
        work = self.work()
        if self.active or work['gaps'] or any(work['counts'].values()):
            if value['purpose'] == 'distribution-update':
                self.db.execute('UPDATE owner_quiescence_receipts SET value=? WHERE fence=?',
                                (json.dumps({**value, 'state': 'not-acquired'}), value['fenceId']))
                self.db.commit()
            return {'held': False, 'executed': False, 'activeRequests': self.active, **work}
        self.save(value)
        self.live_fence = value['fenceId']
        return {'held': True, 'ownerId': owner_id, 'fenceId': value['fenceId']}

    def abort_admission(self, owner_id, context, proof):
        """Settle one recorded pre-retirement attempt; absence never proves refusal.

        The private host channel supplies authenticated supervisor authority.
        Retirement gaps do not prohibit unwinding an attempt: no peer is retired.
        Issued/background work still prevents any new abort settlement.
        """
        token(owner_id, 'owner identity')
        if not isinstance(context, dict) or context.get('purpose') != 'distribution-update' or context.get('serviceIdentity') is not None:
            raise ValueError('Exact distribution admission abort context required.')
        fields = ('commandId', 'fenceId', 'instanceId', 'dataScope')
        binding = {key: token(context.get(key), key) for key in fields}
        if (not isinstance(proof, dict) or set(proof) != {*fields, 'kind', 'verified', 'purpose', 'receiptId'}
                or proof.get('kind') != 'distribution-admission-abort' or proof.get('verified') is not True
                or proof.get('purpose') != 'distribution-update' or any(proof.get(key) != binding[key] for key in fields)):
            raise ValueError('Exact authenticated MCP admission abort proof required.')
        token(proof['receiptId'], 'admission abort proof receipt')
        signature = json.dumps(proof, sort_keys=True, separators=(',', ':'), allow_nan=False)
        row = self.db.execute('SELECT value FROM owner_quiescence_receipts WHERE fence=?', (binding['fenceId'],)).fetchone()
        prior = json.loads(row[0]) if row else None
        if (not prior or prior.get('admissionAttemptVersion') != 1 or prior.get('ownerId') != owner_id
                or prior.get('purpose') != 'distribution-update' or any(prior.get(key) != binding[key] for key in fields)):
            raise ValueError('Original durable MCP admission attempt unavailable; absence is not proof.')
        old = self.current()
        if old and (old.get('ownerId') != owner_id or old.get('purpose') != 'distribution-update'
                    or any(old.get(key) != binding[key] for key in fields)):
            raise ValueError('A different MCP fence requires reconciliation.')
        work = self.work()
        if self.active or any(work['counts'].values()):
            raise ValueError('MCP admission work is still pending; intake was not changed.')
        if prior.get('state') == 'admission-aborted':
            if old or prior.get('abortProofSignature') != signature:
                raise ValueError('MCP admission abort differs from its retained proof.')
            return json.loads(json.dumps(prior['abortReceipt']))
        if old:
            if (old.get('admissionAttemptVersion') != 1 or old.get('state') not in {'held', 'unknown'}
                    or prior.get('state') not in {'held', 'unknown'}):
                raise ValueError('Original held MCP admission attempt unavailable.')
            status = 'released'
        else:
            if prior.get('state') != 'not-acquired':
                raise ValueError('Original durable MCP refusal unavailable; missing held row is not proof.')
            status = 'not-acquired'
        receipt = {**binding, 'ownerId': owner_id, 'status': status,
                   'receiptId': 'mcp-admission-abort:' + hashlib.sha256(json.dumps([owner_id, binding, proof], sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        settled = {**prior, 'state': 'admission-aborted', 'abortProofSignature': signature, 'abortReceipt': receipt}
        # One existing authority transaction stores the receipt and clears intake.
        # A lost reply/restart reads this exact record instead of repeating work.
        with self.db:
            self.db.execute('UPDATE owner_quiescence_receipts SET value=? WHERE fence=?', (json.dumps(settled), binding['fenceId']))
            if old:self.db.execute('DELETE FROM owner_quiescence WHERE id=1')
        self.live_fence = None
        self.on_idle()
        return receipt

    def release(self, owner_id, context, outcome, proof):
        if isinstance(proof, dict) and proof.get('kind') == 'distribution-admission-abort':
            raise ValueError('Admission abort authority cannot authorize generic MCP release.')
        fence_id = token(context.get('fenceId'), 'fence identity')
        signature=json.dumps([outcome,proof],sort_keys=True,separators=(',',':'),allow_nan=False)
        old = self.current()
        if not old:
            row = self.db.execute('SELECT value FROM owner_quiescence_receipts WHERE fence=?', (fence_id,)).fetchone()
            prior = json.loads(row[0]) if row else None
            if prior and prior.get('state') == 'released' and prior.get('ownerId') == owner_id and all(prior.get(key)==context.get(key) for key in ('fenceId','commandId','purpose','instanceId','dataScope')) and prior.get('serviceIdentity')==context.get('serviceIdentity') and prior.get('releaseSignature')==signature:
                return {'released': True, 'duplicate': True}
            raise ValueError('Exact retained MCP fence required.')
        if old['ownerId'] != owner_id or old['fenceId'] != fence_id or any(old.get(key) != context.get(key) for key in ('commandId','purpose','instanceId','dataScope')) or old.get('serviceIdentity')!=context.get('serviceIdentity'):
            raise ValueError('MCP fence context differs.')
        if outcome == 'unknown':
            self.live_fence = None
            old['state'] = 'unknown'; self.save(old)
            return {'released': False, 'intakeClosed': True}
        refused = proof == {'kind': 'admission-refused'} and old['state'] == 'held' and outcome == 'unchanged' and (old['purpose']!='service-stop' or self.live_fence==fence_id)
        if old['purpose']=='service-stop' and not refused:proof=service_proof(proof,old,outcome)
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
        self.live_fence = None
        return {'released': True, 'intakeClosed': False}
