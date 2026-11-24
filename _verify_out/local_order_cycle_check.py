# 로컬 전용 검증: product-settings 화면 렌더 + order_cycle 저장/동기화
# DATABASE_URL="" + QR_DB_PATH=로컬 sqlite만 사용. 운영 DB 미접촉.
import os, sys, sqlite3, json

DB = "_test_order_cycle.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = ""
os.environ["QR_DB_PATH"] = DB
sys.path.insert(0, ".")

import main  # init_db() 실행됨 (product_master는 미생성 -> 아래에서 수동 생성)
from db import get_conn, USE_POSTGRES
from auth.login import create_session

assert not USE_POSTGRES, "Postgres 연결됨 - 중단"
print("DB backend: SQLite ->", DB)

conn = get_conn()
# product_master 수동 생성 (운영 스키마와 동일 컬럼 + order_cycle)
conn.execute("""
CREATE TABLE product_master (
  id TEXT, branch_name TEXT, category TEXT, item_name TEXT, item_code TEXT,
  purchase_price TEXT, sale_price TEXT, tax_setting TEXT, lead_time_days TEXT,
  supplier TEXT, unit TEXT, moq TEXT, is_consumable TEXT, updated_at TEXT,
  product_type TEXT, consumable_type TEXT, order_excluded TEXT, min_safety_qty TEXT,
  order_cycle TEXT
)""")
# menu_permission은 코드에서 CREATE하지 않고 운영 DB에만 존재 -> 로컬 테스트용 빈 테이블
conn.execute("""
CREATE TABLE IF NOT EXISTS menu_permission (
  login_id TEXT, menu_key TEXT, enabled INTEGER, updated_at TEXT
)""")
# 화면이 참조하는 prod 전용 테이블 (스냅샷 스키마 기준, 빈 상태로 생성)
conn.execute("""
CREATE TABLE IF NOT EXISTS purchase_order_monthly_exception (
  id TEXT, item_code TEXT, item_name TEXT, branch_code TEXT, created_at TEXT
)""")
conn.execute("""
CREATE TABLE IF NOT EXISTS eod_alert_exclusion (
  id TEXT, item_code TEXT, item_name TEXT, branch_code TEXT, created_at TEXT
)""")
# accounts에 prod 전용 컬럼 보강 (init_auth_db는 branch_type/team을 만들지 않음)
for col in ("branch_type", "team"):
    try:
        conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} TEXT")
    except Exception:
        pass
conn.execute("UPDATE accounts SET branch_type='branch' WHERE role='branch' AND branch_type IS NULL")
now = "2026-11-24T00:00:00"
# 같은 품번 T001을 두 지점에, T002를 한 지점에
rows = [
    ("1", "경기 김포점", "T001", "테스트상품A"),
    ("2", "부산점",     "T001", "테스트상품A"),
    ("3", "김해점",     "T002", "테스트상품B"),
]
for rid, bn, ic, inm in rows:
    conn.execute(
        "INSERT INTO product_master (id, branch_name, item_name, item_code, purchase_price, lead_time_days, moq, is_consumable, order_excluded, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, bn, inm, ic, "1000", "14", "1", "1", "0", now))
conn.commit()

# 마스터 세션
token = create_session("admin_hq", "master", None)
print("session token created")

from fastapi.testclient import TestClient
client = TestClient(main.app)
client.cookies.set("session_token", token)

results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, extra)

# 1) 화면 렌더
r = client.get("/master/purchase-order/product-settings")
check("GET product-settings 200", r.status_code == 200, f"status={r.status_code}")
html = r.text
check("발주 방식 컬럼 헤더", "발주 방식" in html)
check("pm-cycle select 존재", 'class="pm-cycle"' in html)
check("정기(격주) 옵션", "정기(격주)" in html)
check("월 발주 옵션", "월 발주" in html)
check("미발주 옵션", "미발주" in html)
check("pmBulkCycle 존재", 'id="pmBulkCycle"' in html)
check("pmCycle 추가폼 존재", 'id="pmCycle"' in html)
check("안내 문구", "품번 단위로 모든 지점에 함께 저장" in html)

# JS 추출 -> node --check 용 파일
import re, pathlib
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
js_path = "_verify_out/_rendered_pm_scripts.js"
pathlib.Path(js_path).write_text("\n;\n".join(scripts), encoding="utf-8")
print("extracted", len(scripts), "script blocks ->", js_path)

# 2) 단건 저장: p1에 monthly -> 같은 품번 p2도 동기화
r = client.post("/master/purchase-order/product-master/save", json={
    "id": "1", "purchase_price": 1000, "lead_time_days": 14, "moq": 1,
    "is_consumable": True, "order_cycle": "monthly"})
check("save monthly 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
vals = conn.execute("SELECT id, order_cycle, order_excluded FROM product_master WHERE item_code='T001' ORDER BY id").fetchall()
print("  T001 rows:", [dict(v) for v in vals])
check("monthly 동기 저장(p1,p2)", all(v["order_cycle"] == "monthly" for v in vals) and len(vals) == 2)
check("monthly 시 order_excluded=0", all(str(v["order_excluded"]) in ("0", "0.0") for v in vals))

# 3) 미발주 저장 -> order_excluded=1 동기화
r = client.post("/master/purchase-order/product-master/save", json={
    "id": "2", "purchase_price": 1000, "lead_time_days": 14, "moq": 1,
    "is_consumable": True, "order_cycle": "none"})
check("save none 200", r.status_code == 200)
vals = conn.execute("SELECT id, order_cycle, order_excluded FROM product_master WHERE item_code='T001' ORDER BY id").fetchall()
print("  T001 rows:", [dict(v) for v in vals])
check("none 동기 저장", all(v["order_cycle"] == "none" for v in vals))
check("none 시 order_excluded=1", all(str(v["order_excluded"]) in ("1", "1.0") for v in vals))

# 4) weekly 복귀 -> order_excluded=0
r = client.post("/master/purchase-order/product-master/save", json={
    "id": "1", "purchase_price": 1000, "lead_time_days": 14, "moq": 1,
    "is_consumable": True, "order_cycle": "weekly"})
vals = conn.execute("SELECT order_cycle, order_excluded FROM product_master WHERE item_code='T001'").fetchall()
check("weekly 복귀 시 excluded 해제", all(v["order_cycle"] == "weekly" and str(v["order_excluded"]) in ("0","0.0") for v in vals))

# 5) 잘못된 값 -> 400
r = client.post("/master/purchase-order/product-master/save", json={
    "id": "1", "purchase_price": 1000, "lead_time_days": 14, "moq": 1,
    "is_consumable": True, "order_cycle": "bogus"})
check("invalid cycle 400", r.status_code == 400, f"status={r.status_code}")

# 6) bulk-apply: p3에 monthly
r = client.post("/master/purchase-order/product-master/bulk-apply", json={"ids": ["3"], "order_cycle": "monthly"})
check("bulk-apply monthly", r.status_code == 200 and r.json().get("success") == 1, f"{r.status_code} {r.text[:200]}")
v = conn.execute("SELECT order_cycle, order_excluded FROM product_master WHERE id='3'").fetchone()
check("p3 monthly 저장", v["order_cycle"] == "monthly")

# 7) bulk-save: 행 단위 order_cycle
r = client.post("/master/purchase-order/product-master/bulk-save", json={"rows": [
    {"id": "1", "purchase_price": 1000, "lead_time_days": 14, "moq": 1, "is_consumable": True,
     "order_cycle": "none", "category": "", "product_type": "", "consumable_type": ""}]})
check("bulk-save none", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
vals = conn.execute("SELECT order_cycle, order_excluded FROM product_master WHERE item_code='T001'").fetchall()
check("bulk-save none 동기화", all(v["order_cycle"] == "none" and str(v["order_excluded"]) in ("1","1.0") for v in vals))

# 8) 키 없는 저장은 order_cycle 유지 (min_safety_qty와 같은 '제공 시에만' 규칙)
r = client.post("/master/purchase-order/product-master/save", json={
    "id": "3", "purchase_price": 2000, "lead_time_days": 20, "moq": 2,
    "is_consumable": True})
v = conn.execute("SELECT order_cycle FROM product_master WHERE id='3'").fetchone()
check("order_cycle 키 없으면 유지", v["order_cycle"] == "monthly", f"got={v['order_cycle']}")

conn.close()
fails = [n for n, ok in results if not ok]
print("\n==== RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}", "====")
sys.exit(1 if fails else 0)
