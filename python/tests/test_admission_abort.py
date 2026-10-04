"""Inert owned stores only: no install, credential scan or external execution."""
import json
import sqlite3
import pytest
from amplifier_unified_mcp.broker import Broker


def context():
    return dict(fenceId='abort-fence', commandId='abort-command', purpose='distribution-update',
                instanceId='host-original', dataScope='owned-fixture')


def proof(c):
    return {**c, 'kind':'distribution-admission-abort', 'verified':True, 'receiptId':'supervisor-original-abort'}


def assert_receipt(receipt,c,status):
    assert set(receipt)=={'ownerId','status','receiptId','fenceId','commandId','instanceId','dataScope'}
    assert receipt['ownerId']=='mcp' and receipt['status']==status and receipt['receiptId']
    assert all(receipt[key]==c[key] for key in ('fenceId','commandId','instanceId','dataScope'))


async def test_lost_reply_restart_exact_receipt_and_no_reexecution(tmp_path):
    c=context();b=Broker(tmp_path)
    assert b.quiescence.acquire('mcp',c)['held'];await b.close()
    b=Broker(tmp_path)
    assert b.quiescence.current()['state']=='unknown'
    receipt=b.quiescence.abort_admission('mcp',c,proof(c)) # settled, caller discards reply
    assert_receipt(receipt,c,'released');assert not b.quiescence.current();await b.close()
    b=Broker(tmp_path)
    try:
        # An exact reply recovery must not UPDATE/DELETE authority again.
        b.db.execute("CREATE TEMP TRIGGER no_abort_repeat BEFORE UPDATE ON owner_quiescence_receipts BEGIN SELECT RAISE(ABORT,'unexpected repeated settlement');END")
        assert b.quiescence.abort_admission('mcp',c,proof(c))==receipt
        with pytest.raises(ValueError,match='again'):b.quiescence.acquire('mcp',c)
        assert not b.db.execute('SELECT 1 FROM commands').fetchone()
        assert not b.manager.connections
    finally:await b.close()


async def test_recorded_busy_refusal_survives_restart_without_absence_inference(tmp_path):
    c=context();b=Broker(tmp_path);b.quiescence.active=1
    assert b.quiescence.acquire('mcp',c)['held'] is False
    assert json.loads(b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0])['state']=='not-acquired'
    with pytest.raises(ValueError,match='pending'):b.quiescence.abort_admission('mcp',c,proof(c))
    b.quiescence.active=0;await b.close();b=Broker(tmp_path)
    try:
        receipt=b.quiescence.abort_admission('mcp',c,proof(c));assert_receipt(receipt,c,'not-acquired')
        assert b.quiescence.abort_admission('mcp',c,proof(c))==receipt
    finally:await b.close()


@pytest.mark.parametrize('key',['fenceId','commandId','instanceId','dataScope','receiptId'])
async def test_changed_proof_preserves_held_or_retained_receipt(tmp_path,key):
    c=context();b=Broker(tmp_path)
    try:
        assert b.quiescence.acquire('mcp',c)['held']
        bad={**proof(c),key:'different'}
        if key!='receiptId':
            with pytest.raises(ValueError,match='proof'):b.quiescence.abort_admission('mcp',c,bad)
            assert b.quiescence.current()
        receipt=b.quiescence.abort_admission('mcp',c,proof(c))
        with pytest.raises(ValueError,match='proof'):b.quiescence.abort_admission('mcp',c,bad)
        assert b.quiescence.abort_admission('mcp',c,proof(c))==receipt
    finally:await b.close()


@pytest.mark.parametrize('bad',[None,{}, {'kind':'admission-refused'}, {'verified':True,'kind':'distribution-admission-abort'}])
async def test_weak_or_generic_proof_never_clears_intake(tmp_path,bad):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c)
        with pytest.raises(ValueError,match='proof'):b.quiescence.abort_admission('mcp',c,bad)
        release={**c,'verified':True,'outcome':'unchanged','receiptId':'generic-release'}
        with pytest.raises(ValueError,match='proof'):b.quiescence.abort_admission('mcp',c,release)
        with pytest.raises(ValueError,match='generic MCP release'):b.quiescence.release('mcp',c,'unchanged',{**proof(c),'outcome':'unchanged'})
        assert b.quiescence.current()
    finally:await b.close()


@pytest.mark.parametrize('kind',['no-receipt','legacy-unknown','missing-held','attempt-only'])
async def test_missing_or_legacy_evidence_remains_unknown(tmp_path,kind):
    c=context();b=Broker(tmp_path)
    try:
        if kind!='no-receipt':
            b.quiescence.acquire('mcp',c)
            if kind=='legacy-unknown':
                old=b.quiescence.current();old.pop('admissionAttemptVersion');old['state']='unknown';b.quiescence.save(old)
            elif kind=='attempt-only':
                old=b.quiescence.current();old['state']='attempted';b.db.execute('UPDATE owner_quiescence_receipts SET value=?',(json.dumps(old),));b.db.execute('DELETE FROM owner_quiescence');b.db.commit()
            else:b.db.execute('DELETE FROM owner_quiescence');b.db.commit()
        with pytest.raises(ValueError,match='unavailable|absence|missing held'):b.quiescence.abort_admission('mcp',c,proof(c))
        assert not b.db.execute("SELECT 1 FROM owner_quiescence_receipts WHERE json_extract(value,'$.state')='admission-aborted'").fetchone()
    finally:await b.close()


async def test_atomic_receipt_and_clear_rollback_on_storage_failure(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c);before=b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]
        b.db.execute("CREATE TEMP TRIGGER refuse_clear BEFORE DELETE ON owner_quiescence BEGIN SELECT RAISE(ABORT,'owned clear failure');END")
        with pytest.raises(sqlite3.IntegrityError,match='owned clear failure'):b.quiescence.abort_admission('mcp',c,proof(c))
        assert b.quiescence.current() and b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]==before
        b.db.execute('DROP TRIGGER refuse_clear')
        assert_receipt(b.quiescence.abort_admission('mcp',c,proof(c)),c,'released')
    finally:await b.close()


async def test_changed_owner_current_fence_or_pending_work_cannot_unwind(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c)
        with pytest.raises(ValueError,match='unavailable'):b.quiescence.abort_admission('other-owner',c,proof(c))
        b.manager.background_io.add('owned-pending')
        with pytest.raises(ValueError,match='pending'):b.quiescence.abort_admission('mcp',c,proof(c))
        b.manager.background_io.clear();b.quiescence.abort_admission('mcp',c,proof(c))
        other={**c,'fenceId':'new-fence','commandId':'new-command'};b.quiescence.acquire('mcp',other)
        receipt=b.quiescence.abort_admission('mcp',c,proof(c))
        assert_receipt(receipt,c,'released')
        assert b.quiescence.current()['fenceId']=='new-fence'
    finally:await b.close()


async def test_abort_unwinds_recorded_retirement_gap_without_retiring_any_peer(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.work=lambda:{'counts':{},'gaps':['connected-peer-retirement-unverified']}
        assert b.quiescence.acquire('mcp',c)['held'] is False
        assert_receipt(b.quiescence.abort_admission('mcp',c,proof(c)),c,'not-acquired')
        assert not b.quiescence.current()
    finally:await b.close()


async def test_abort_preserves_original_unknown_smart_tool_command(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        value=json.dumps({'id':'original-tool-command','status':'interrupted','outcome':'unknown'})
        with b.db:b.db.execute('INSERT INTO commands VALUES(?,?,?,?)',('original-tool-command','host','original',value))
        b.quiescence.acquire('mcp',c);b.quiescence.abort_admission('mcp',c,proof(c))
        assert b.db.execute('SELECT value FROM commands WHERE id=?',('original-tool-command',)).fetchone()[0]==value
    finally:await b.close()


async def test_partial_unwind_restart_settles_released_with_new_abort_authority(tmp_path):
    c=context();b=Broker(tmp_path)
    assert b.quiescence.acquire('mcp',c)['held']
    assert b.quiescence.release('mcp',c,'unchanged',{'kind':'admission-refused'})['released']
    retained=b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]
    await b.close();b=Broker(tmp_path)
    try:
        with pytest.raises(ValueError,match='proof'):
            b.quiescence.abort_admission('mcp',c,{'kind':'admission-refused'})
        assert b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]==retained
        receipt=b.quiescence.abort_admission('mcp',c,proof(c))
        assert_receipt(receipt,c,'released')
        b.db.execute("CREATE TEMP TRIGGER no_partial_repeat BEFORE UPDATE ON owner_quiescence_receipts BEGIN SELECT RAISE(ABORT,'repeated partial unwind');END")
        assert b.quiescence.abort_admission('mcp',c,proof(c))==receipt
        with pytest.raises(ValueError,match='proof'):
            b.quiescence.abort_admission('mcp',c,{**proof(c),'receiptId':'different'})
    finally:await b.close()


@pytest.mark.parametrize('change',['signature-missing','signature-generic','signature-ready','outcome-unknown','attempt-marker-missing'])
async def test_partial_unwind_incomplete_or_generic_evidence_refuses(tmp_path,change):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c)
        b.quiescence.release('mcp',c,'unchanged',{'kind':'admission-refused'})
        prior=json.loads(b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0])
        if change=='signature-missing':prior.pop('releaseSignature')
        elif change=='signature-generic':prior['releaseSignature']=json.dumps(['unchanged',{**c,'verified':True,'outcome':'unchanged','receiptId':'generic-release'}],sort_keys=True,separators=(',',':'))
        elif change=='signature-ready':prior['releaseSignature']=json.dumps(['ready',{'kind':'admission-refused'}],sort_keys=True,separators=(',',':'))
        elif change=='outcome-unknown':prior['outcome']='unknown'
        else:prior.pop('admissionAttemptVersion')
        before=json.dumps(prior)
        with b.db:b.db.execute('UPDATE owner_quiescence_receipts SET value=?',(before,))
        with pytest.raises(ValueError,match='unavailable'):
            b.quiescence.abort_admission('mcp',c,proof(c))
        assert b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]==before
        assert not b.quiescence.current()
    finally:await b.close()


async def test_partial_unwind_preserves_newer_unrelated_hold(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c)
        b.quiescence.release('mcp',c,'unchanged',{'kind':'admission-refused'})
        other={**c,'fenceId':'newer-fence','commandId':'newer-command'}
        b.quiescence.acquire('mcp',other)
        held=b.db.execute('SELECT value FROM owner_quiescence').fetchone()[0]
        receipts=b.db.execute('SELECT fence,value FROM owner_quiescence_receipts ORDER BY fence').fetchall()
        receipt=b.quiescence.abort_admission('mcp',c,proof(c))
        assert_receipt(receipt,c,'released')
        assert b.db.execute('SELECT value FROM owner_quiescence').fetchone()[0]==held
        assert b.quiescence.live_fence==other['fenceId']
        assert b.db.execute('SELECT value FROM owner_quiescence_receipts WHERE fence=?',(other['fenceId'],)).fetchone()[0]==dict(receipts)[other['fenceId']]
        b.quiescence.active=1;b.manager.background_io.add('newer-work')
        assert b.quiescence.abort_admission('mcp',c,proof(c))==receipt
        assert b.db.execute('SELECT value FROM owner_quiescence').fetchone()[0]==held
        b.quiescence.active=0;b.manager.background_io.clear()
    finally:await b.close()


async def test_passive_node_observation_never_clears_matching_live_hold(tmp_path):
    c=context();b=Broker(tmp_path)
    try:
        b.quiescence.acquire('mcp',c)
        held=b.db.execute('SELECT value FROM owner_quiescence').fetchone()[0]
        prior=b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]
        with pytest.raises(ValueError,match='passive'):
            b.quiescence.abort_admission('mcp',c,proof(c),passive_only=True)
        assert b.db.execute('SELECT value FROM owner_quiescence').fetchone()[0]==held
        assert b.db.execute('SELECT value FROM owner_quiescence_receipts').fetchone()[0]==prior
    finally:await b.close()
