# order_cycle 도입 후 발주 후보 분류 동등성 검증 (스냅샷, 읽기 전용)
# 구 규칙: monthly = lead>=35 or monthly_exception
# 신 규칙: effective_order_cycle(order_cycle=NULL, ...) — order_cycle이 전부 NULL이면 동일해야 함
import sqlite3, math, sys
from collections import defaultdict
sys.path.insert(0, '.')
from order_policy import effective_order_cycle

SNAP = '_verify_out/snapshot_20261124.db'
conn = sqlite3.connect(SNAP)
conn.row_factory = sqlite3.Row

# 스냅샷에 order_cycle 컬럼이 없으므로 NULL로 간주 (SELECT *에 없음 -> .get 사용)
has_oc = 'order_cycle' in [r[1] for r in conn.execute('PRAGMA table_info(product_master)')]
print('snapshot has order_cycle column:', has_oc)

safety_rows = conn.execute("SELECT branch_name, item_name, item_code, qty, moq FROM safety_stock WHERE qty > 0").fetchall()
pm = {}
for r in conn.execute("SELECT * FROM product_master WHERE item_code IS NOT NULL AND item_code != '' ORDER BY item_code, id"):
    pm.setdefault(r['item_code'], r)

raw_map = {}
for r in conn.execute("SELECT branch_code, item_code, quantity FROM raw_inventory"):
    try: raw_map[(r['branch_code'], r['item_code'])] = int(r['quantity'] or 0)
    except Exception: raw_map[(r['branch_code'], r['item_code'])] = 0

codes = {r['branch_code'] for r in conn.execute('SELECT DISTINCT branch_code FROM raw_inventory')}
def b2c(name):
    n = name.replace(' ', '')
    return n if n in codes else (name if name in codes else None)

monthly_exc = conn.execute("SELECT item_code, item_name, branch_code FROM purchase_order_monthly_exception").fetchall()
mexc_code = {(r['item_code'], r['branch_code']) for r in monthly_exc if r['item_code']}
mexc_name = {(r['item_name'], r['branch_code']) for r in monthly_exc if not r['item_code']}

status_map = {}
for r in conn.execute("SELECT branch_name, item_name, status, purchase_datetime FROM purchase_records ORDER BY purchase_datetime DESC"):
    status_map.setdefault((r['branch_name'], r['item_name']), r['status'])

old_w, old_m, new_w, new_m = [], [], [], []
skipped = defaultdict(int)
mismatch = []
PENDING = {'접수', '승인'}
for row in safety_rows:
    bn, it, ic = row['branch_name'], row['item_name'], row['item_code'] or ''
    sq = int(row['qty'] or 0)
    bc = b2c(bn)
    if not bc:
        skipped['branch_map_fail'] += 1; continue
    p = pm.get(ic)
    if p and str(p['order_excluded']) in ('1', 't', 'T'):
        skipped['order_excluded'] += 1; continue
    cur = raw_map.get((bc, ic), 0) or 0
    if cur >= sq:
        skipped['stock_enough'] += 1; continue
    if not (p and str(p['is_consumable']) in ('1', 't', 'T')):
        skipped['not_consumable'] += 1; continue
    st = status_map.get((bn, it))
    if st in PENDING:
        skipped['pending_' + str(st)] += 1; continue
    lead = int(p['lead_time_days']) if p and p['lead_time_days'] not in (None, '') else 0
    is_exc = (ic, bc) in mexc_code or (it, bc) in mexc_name
    oc = p['order_cycle'] if (p and has_oc) else None
    old_monthly = lead >= 35 or is_exc
    new_cycle = effective_order_cycle(oc, order_excluded=False, lead_time_days=lead, monthly_exception=is_exc)
    if (old_monthly and new_cycle != 'monthly') or (not old_monthly and new_cycle != 'weekly'):
        mismatch.append((bn, it, ic, lead, is_exc, oc, old_monthly, new_cycle))
    cand = {'branch': bn, 'item': it, 'icode': ic}
    if old_monthly: old_m.append(cand)
    else: old_w.append(cand)
    if new_cycle == 'monthly': new_m.append(cand)
    else: new_w.append(cand)

print('skipped:', dict(skipped))
print('OLD weekly/monthly:', len(old_w), len(old_m), 'total', len(old_w)+len(old_m))
print('NEW weekly/monthly:', len(new_w), len(new_m), 'total', len(new_w)+len(new_m))
print('classification mismatches:', len(mismatch))
for m in mismatch[:10]: print('  MISMATCH', m)
# 행 단위 동일성
same = sorted((c['branch'], c['item'], c['icode']) for c in old_w) == sorted((c['branch'], c['item'], c['icode']) for c in new_w) \
   and sorted((c['branch'], c['item'], c['icode']) for c in old_m) == sorted((c['branch'], c['item'], c['icode']) for c in new_m)
print('ROW-LEVEL IDENTICAL:', same)

# order_cycle 명시값 동작 확인 (합성 케이스)
print('\n--- synthetic order_cycle behavior ---')
cases = [
    ('weekly 명시 + lead35', 'weekly', False, 35, False, 'weekly'),
    ('monthly 명시 + lead14', 'monthly', False, 14, False, 'monthly'),
    ('none 명시 + lead35', 'none', False, 35, False, 'none'),
    ('NULL + lead35', None, False, 35, False, 'monthly'),
    ('NULL + 예외', None, False, 14, True, 'monthly'),
    ('weekly + 예외', 'weekly', False, 14, True, 'monthly'),
]
ok = True
for name, oc, ex, lead, exc, want in cases:
    got = effective_order_cycle(oc, order_excluded=ex, lead_time_days=lead, monthly_exception=exc)
    status = 'OK' if got == want else 'FAIL'
    if got != want: ok = False
    print(f'  {status} {name}: got={got} want={want}')
print('synthetic all pass:', ok)
conn.close()
