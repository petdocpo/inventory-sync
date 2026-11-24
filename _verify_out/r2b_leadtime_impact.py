# R2(b): lead_time_days 전환(전부 14일) 전후 안전재고 추천값 비교 — 스냅샷 기반 독립 재현
import sqlite3, math, json
from collections import defaultdict

DB = '_verify_out/snapshot_20261124.db'
FACTOR = 1.05
MIN_QTY = 1
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# product_master: item_code별 대표행 (DISTINCT ON item_code ... ORDER BY item_code, id 와 동일 효과)
pm = {}
for r in conn.execute("SELECT * FROM product_master WHERE item_code IS NOT NULL AND item_code <> '' ORDER BY item_code, id"):
    pm.setdefault(r['item_code'], r)

# 기간: sales_records 전체 보관 범위 (compare 화면 기본값과 동일)
rng = conn.execute("SELECT MIN(sale_date) d0, MAX(sale_date) d1 FROM sales_records").fetchone()
from datetime import date
d0 = date.fromisoformat(rng['d0']); d1 = date.fromisoformat(rng['d1'])
days = (d1 - d0).days + 1
print(f"period {rng['d0']}~{rng['d1']} = {days}일, factor={FACTOR}, min={MIN_QTY}")

sold = defaultdict(int)
for r in conn.execute("SELECT branch_name, item_code, SUM(quantity) s FROM sales_records WHERE item_code<>'' GROUP BY branch_name, item_code"):
    sold[(r['branch_name'], r['item_code'])] = int(r['sold'] if 'sold' in r.keys() else r['s'] or 0)

safety = conn.execute("SELECT * FROM safety_stock WHERE branch_name<>'본사'").fetchall()

def lead_of(p):
    if p is None: return 0
    v = p['lead_time_days']
    try: return int(v) if v not in (None,'') else 0
    except: return 0

def min_used_of(p):
    if p is not None and p['min_safety_qty'] not in (None,''):
        try: return int(float(p['min_safety_qty']))
        except: pass
    return MIN_QTY

def rec(lead, s, days_):
    if s <= 0 or lead <= 0: return None
    avg = s / days_
    return max(MIN_QTY, math.ceil(round(avg * lead * FACTOR, 6)))

rows = []
for sr in safety:
    code = sr['item_code'] or ''
    p = pm.get(code)
    s = sold.get((sr['branch_name'], code), 0)
    lead = lead_of(p)
    cur = rec(lead, s, days)
    new = rec(14, s, days)   # 전부 14일 가정
    if cur is None and new is None: continue
    rows.append({
        'branch': sr['branch_name'], 'item': sr['item_name'], 'code': code,
        'lead': lead, 'sold': s, 'cur_rec': cur, 'new_rec': new,
        'cur_qty': sr['qty'], 'moq': sr['moq'],
        'consumable': bool(p and str(p['is_consumable']) in ('1','true','True')),
        'excluded': bool(p and str(p['order_excluded']) in ('1','true','True')),
    })

# 요약
def summarize(rs, label):
    chg = [r for r in rs if r['cur_rec'] is not None and r['new_rec'] is not None and r['cur_rec'] != r['new_rec']]
    up = [r for r in chg if r['new_rec'] > r['cur_rec']]
    dn = [r for r in chg if r['new_rec'] < r['cur_rec']]
    zero_lead = [r for r in rs if r['lead'] == 0 and r['sold'] > 0]
    lt35 = [r for r in rs if r['lead'] >= 35]
    print(f"\n=== {label}: rows={len(rs)}, 추천값 변동={len(chg)} (상향 {len(up)} / 하향 {len(dn)}), lead=0&판매있음={len(zero_lead)}, lead>=35={len(lt35)}")
    if chg:
        diffs = sorted(((r['new_rec']-r['cur_rec'], r) for r in chg), key=lambda x: -abs(x[0]))
        for d, r in diffs[:15]:
            print(f"  {d:+d}  {r['branch']} | {r['item']} | lead {r['lead']}→14 sold={r['sold']} rec {r['cur_rec']}→{r['new_rec']} qty={r['cur_qty']}")
    if zero_lead:
        zl = sorted(zero_lead, key=lambda r: -r['sold'])[:10]
        for r in zl:
            print(f"  [lead0] {r['branch']} | {r['item']} | sold={r['sold']} qty={r['cur_qty']} consumable={r['consumable']}")
    return {'rows': len(rs), 'changed': len(chg), 'up': len(up), 'down': len(dn),
            'zero_lead_sold': len(zero_lead), 'lt35': len(lt35)}

cons = [r for r in rows if r['consumable']]
nonc = [r for r in rows if not r['consumable']]
res = {
    'period': f"{rng['d0']}~{rng['d1']}", 'days': days,
    'all': summarize(rows, '전체(비본사)'),
    'consumable': summarize(cons, '소모품(is_consumable)'),
    'nonconsumable': summarize(nonc, '비소모품'),
}
# lead 분포별
print('\n--- lead 분포(판매>0 safety 행 기준) ---')
ld = defaultdict(lambda: [0,0])
for r in rows:
    if r['sold']>0:
        ld[r['lead']][0]+=1
        if r['cur_rec'] is not None and r['new_rec'] is not None and r['cur_rec']!=r['new_rec']: ld[r['lead']][1]+=1
for k in sorted(ld): print(f"  lead={k}: {ld[k][0]}행, 변동 {ld[k][1]}")

with open('_verify_out/r2b_leadtime14_impact.json','w',encoding='utf-8') as f:
    json.dump({'summary':res,'detail':[dict(r) for r in rows]}, f, ensure_ascii=False, indent=1)
print('\nsaved _verify_out/r2b_leadtime14_impact.json')
conn.close()
