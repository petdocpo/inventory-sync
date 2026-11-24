# R1/R3 보조: 스냅샷으로 _compute_purchase_order_candidates 동등 재현 + 월간 예외 현황
import sqlite3, math, json
from collections import defaultdict

conn = sqlite3.connect('_verify_out/snapshot_20261124.db')
conn.row_factory = sqlite3.Row

safety_rows = conn.execute("SELECT branch_name, item_name, item_code, qty, moq FROM safety_stock WHERE qty > 0").fetchall()
pm = {}
for r in conn.execute("SELECT * FROM product_master WHERE item_code IS NOT NULL AND item_code != '' ORDER BY item_code, id"):
    pm.setdefault(r['item_code'], r)

raw_map = {}
for r in conn.execute("SELECT branch_code, item_code, quantity FROM raw_inventory"):
    try: raw_map[(r['branch_code'], r['item_code'])] = int(r['quantity'] or 0)
    except: raw_map[(r['branch_code'], r['item_code'])] = 0

# branch_name -> branch_code: 공백 제거 매핑 (accounts 미스냅샷, raw_inventory code가 공백없음)
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

weekly, monthly, pending, skipped = [], [], [], defaultdict(int)
PENDING = {'접수', '승인'}
for row in safety_rows:
    bn, it, ic = row['branch_name'], row['item_name'], row['item_code'] or ''
    sq = int(row['qty'] or 0)
    bc = b2c(bn)
    if not bc:
        skipped['branch_map_fail'] += 1; continue
    p = pm.get(ic)
    if p and str(p['order_excluded']) in ('1','t','T'):
        skipped['order_excluded'] += 1; continue
    cur = raw_map.get((bc, ic), 0) or 0
    if cur >= sq:
        skipped['stock_enough'] += 1; continue
    cons = bool(p and str(p['is_consumable']) in ('1','t','T'))
    if not cons:
        skipped['not_consumable'] += 1; continue
    st = status_map.get((bn, it))
    if st in PENDING:
        skipped['pending_' + str(st)] += 1
        pending.append({'branch': bn, 'item': it, 'code': ic, 'status': st})
        continue
    moq = int(p['moq']) if p and p['moq'] else 1
    if moq <= 0: moq = 1
    fq = math.ceil(sq / moq) * moq
    lead = int(p['lead_time_days']) if p and p['lead_time_days'] not in (None,'') else 0
    cand = {'branch': bn, 'code': bc, 'item': it, 'icode': ic, 'safety': sq, 'moq': moq, 'qty': fq, 'lead': lead}
    is_monthly = lead >= 35 or (ic, bc) in mexc_code or (it, bc) in mexc_name
    (monthly if is_monthly else weekly).append(cand)

print('safety qty>0 rows:', len(safety_rows))
print('skipped:', dict(skipped))
print('weekly candidates:', len(weekly), ' monthly:', len(monthly), ' pending:', len(pending))
for c in weekly: print(' W', c)
for c in monthly: print(' M', c)
for p in pending[:20]: print(' P', p)

# 월간 예외 지점×품목 표 (45행)
print('\n--- monthly exception 지점별 ---')
byb = defaultdict(list)
for r in monthly_exc:
    byb[r['branch_code']].append(r['item_name'])
for b, items in byb.items():
    print(b, len(items), 'items')

# lead>=35 && safety qty>0 && consumable -> 현재 monthly에 실제로 들어가는 것
print('\n--- monthly candidates detail ---')
for c in monthly: print(c)

conn.close()
