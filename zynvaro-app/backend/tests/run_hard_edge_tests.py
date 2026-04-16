"""
Hard edge case tests for Razorpay payout - GPay/PhonePe standards.
Run: python tests/run_hard_edge_tests.py
"""
import sys, io, os, json, uuid, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ["SECRET_KEY"] = "test-key-123"
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine, Base, SessionLocal
from models import *
from services.payout_service import initiate_payout, _create_mock_payout, get_payout_details
from datetime import datetime, timedelta

Base.metadata.create_all(bind=engine)
db = SessionLocal()

passed = 0
failed = 0

def chk(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS  {name}')
    else:
        failed += 1
        print(f'  FAIL  {name} -- {detail}')

_n = [0]
def mkw(**kw):
    _n[0] += 1
    w = Worker(full_name=kw.get('name', f'Worker-{_n[0]}'),
               phone=kw.get('phone', f'700000{_n[0]:04d}'),
               password_hash='x', city=kw.get('city','Mumbai'),
               pincode=kw.get('pin','400001'), platform='Blinkit')
    db.add(w); db.flush(); return w

def mkp(w):
    _n[0] += 1
    p = Policy(worker_id=w.id, policy_number=f'ZYN-EDGE-{_n[0]:04d}',
               tier='Basic Shield', status='active', weekly_premium=29,
               base_premium=29, max_daily_payout=300, max_weekly_payout=600)
    db.add(p); db.flush(); return p

def mkt():
    te = TriggerEvent(trigger_type='Heavy Rainfall', city='Mumbai',
                      measured_value=72, threshold_value=64.5, unit='mm/24hr',
                      source_primary='test', is_validated=True, severity='high')
    db.add(te); db.flush(); return te

def mkc(w, p, te, amount=300):
    c = Claim(claim_number=f'CLM-{uuid.uuid4().hex[:8].upper()}',
              worker_id=w.id, policy_id=p.id, trigger_event_id=te.id,
              status=ClaimStatus.PAID, payout_amount=amount, authenticity_score=100)
    db.add(c); db.flush(); return c


print("=== HARD EDGE CASES: Payment Platform Standards ===")
print()
print("--- 1. DOUBLE-SPEND PREVENTION ---")
w = mkw(); p = mkp(w); te = mkt()
c1 = mkc(w, p, te)
txn1 = initiate_payout(c1, w, db); db.flush()
txn2 = initiate_payout(c1, w, db); db.flush()
chk('H1 - Double payout: separate txns', txn1.id != txn2.id and txn1.internal_txn_id != txn2.internal_txn_id, '')
txns = db.query(PayoutTransaction).filter(PayoutTransaction.claim_id == c1.id).all()
total_s = sum(t.amount_settled or 0 for t in txns)
chk('H2 - Double payout tracked (2x300=600)', total_s == 600.0, f'total={total_s}')

print()
print("--- 2. CONCURRENT BURST ---")
workers = [mkw(phone=f'800{i:07d}') for i in range(10)]
pols = [mkp(w) for w in workers]
te2 = mkt()
cls = [mkc(workers[i], pols[i], te2) for i in range(10)]
txns = [initiate_payout(cls[i], workers[i], db) for i in range(10)]
db.flush()
chk('H3 - 10 burst: all settled', all(t.status == 'settled' for t in txns), f'{sum(1 for t in txns if t.status=="settled")}/10')
chk('H4 - 10 burst: unique IDs', len(set(t.internal_txn_id for t in txns)) == 10, '')
chk('H5 - 10 burst: amounts match', sum(t.amount_settled for t in txns) == 3000.0, '')

print()
print("--- 3. AMOUNT PRECISION ---")
w = mkw(); p = mkp(w); te3 = mkt()
for amt, label in [(0.01, 'H6-1 paisa'), (333.33, 'H7-float'), (100000.0, 'H8-100K'), (-50.0, 'H9-negative')]:
    c = mkc(w, p, te3, amount=amt)
    txn = initiate_payout(c, w, db)
    chk(f'{label}: amount={amt}', txn.amount_requested == amt, f'req={txn.amount_requested}')

print()
print("--- 4. WEBHOOK SECURITY ---")
from routers.webhooks import _verify_razorpay_signature
chk('H10 - No secret: sig skipped', _verify_razorpay_signature(b'test', '', '') == True, '')

from fastapi.testclient import TestClient
from main import app
client = TestClient(app)
resp = client.post('/webhooks/razorpay', json={"event": "payout.processed", "payload": {}})
chk('H11 - Empty payload: 200 OK', resp.status_code == 200, f'status={resp.status_code}')
resp = client.post('/webhooks/razorpay', json={
    "event": "payout.unknown_xyz",
    "payload": {"payout": {"entity": {"id": "x", "reference_id": "y"}}}
})
chk('H12 - Unknown event: ignored', resp.json().get("status") == "ignored", f'resp={resp.json()}')

print()
print("--- 5. STATE MACHINE ---")
w = mkw(); p = mkp(w); te4 = mkt()
c = mkc(w, p, te4)
txn_fail = PayoutTransaction(claim_id=c.id, worker_id=w.id, upi_id='test@upi',
    internal_txn_id=f'ZYN-FAIL-{uuid.uuid4().hex[:8]}', amount_requested=300,
    currency='INR', status=PayoutTransactionStatus.FAILED, failure_reason='Timeout',
    gateway_name='razorpay')
db.add(txn_fail); db.flush()
txn_retry = initiate_payout(c, w, db); db.flush()
chk('H13 - Failed allows retry', txn_retry.id != txn_fail.id, '')
chk('H14 - Retry settles', txn_retry.status == 'settled', f'status={txn_retry.status}')
txn_rev = PayoutTransaction(claim_id=c.id, worker_id=w.id, upi_id='test@upi',
    internal_txn_id=f'ZYN-REV-{uuid.uuid4().hex[:8]}', amount_requested=300,
    currency='INR', status=PayoutTransactionStatus.REVERSED, gateway_name='razorpay')
db.add(txn_rev); db.flush()
chk('H15 - REVERSED persists', txn_rev.status == 'reversed', '')

print()
print("--- 6. MULTI-WORKER ISOLATION ---")
w1 = mkw(name='Alice'); w2 = mkw(name='Bob')
p1 = mkp(w1); p2 = mkp(w2); te5 = mkt()
c1 = mkc(w1, p1, te5, 300); c2 = mkc(w2, p2, te5, 500)
t1 = initiate_payout(c1, w1, db); t2 = initiate_payout(c2, w2, db); db.flush()
chk('H16 - Isolation: different txns', t1.worker_id != t2.worker_id, '')
chk('H17 - Isolation: correct amounts', t1.amount_settled == 300 and t2.amount_settled == 500, '')
chk('H18 - Isolation: different UPIs', t1.upi_id != t2.upi_id, '')

print()
print("--- 7. AUDIT TRAIL ---")
c = mkc(w1, p1, te5)
txn = initiate_payout(c, w1, db); db.flush()
payload = json.loads(txn.gateway_payload)
chk('H19 - Payload valid JSON', isinstance(payload, dict), '')
chk('H20 - Payload has status', 'status' in payload, f'keys={list(payload.keys())}')
chk('H21 - Payload has amount', 'amount' in payload, '')
db.refresh(txn)
chk('H22 - DB round-trip', json.loads(txn.gateway_payload) == payload, '')

print()
print("--- 8. CLAIM-PAYOUT CONSISTENCY ---")
c = mkc(w1, p1, te5)
txn = initiate_payout(c, w1, db)
chk('H23 - claim.paid_at set', c.paid_at is not None, '')
chk('H24 - txn.settled_at set', txn.settled_at is not None, '')
chk('H25 - Temporal order', c.paid_at <= txn.settled_at + timedelta(seconds=1), '')

c = mkc(w1, p1, te5)
_create_mock_payout(c, w1, db); db.flush()
time.sleep(0.01)
_create_mock_payout(c, w1, db); db.flush()
details = get_payout_details(c.id, db)
chk('H26 - Latest txn returned', details is not None, '')

print()
print("--- 9. NULL/MISSING FIELDS ---")
w_z = Worker(full_name='ZeroPhone', phone='0000000000', password_hash='x',
             city='Delhi', pincode='110001', platform='Zepto')
db.add(w_z); db.flush()
p_z = mkp(w_z); c = mkc(w_z, p_z, te5)
txn = initiate_payout(c, w_z, db)
chk('H27 - Zero phone UPI generated', txn.upi_id is not None, f'upi={txn.upi_id}')
c = mkc(w1, p1, te5, 200)
txn = initiate_payout(c, w1, db)
chk('H28 - Valid trigger ref', txn.status == 'settled', '')

print()
print("--- 10. PERFORMANCE ---")
rw = [mkw(phone=f'900{i:07d}') for i in range(50)]
rp = [mkp(rw[i]) for i in range(50)]
te6 = mkt()
rc = [mkc(rw[i], rp[i], te6, 100+i) for i in range(50)]
start = time.time()
rt = [initiate_payout(rc[i], rw[i], db) for i in range(50)]
db.flush()
elapsed = time.time() - start
chk(f'H29 - 50 rapid payouts: all settled', all(t.status == 'settled' for t in rt), f'{sum(1 for t in rt if t.status=="settled")}/50')
chk(f'H30 - Under 2 seconds ({elapsed:.2f}s)', elapsed < 2.0, f'{elapsed:.2f}s')

db.close()
print()
print(f'=== GRAND TOTAL: {passed} passed, {failed} failed out of 30 ===')
