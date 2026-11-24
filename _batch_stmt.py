import sys, os, re, time
sys.stdout.reconfigure(encoding='utf-8')
import main

BASE = r"C:\Users\PETDOC\OneDrive - (주)펫닥\바탕 화면\거래명세서-세금계산서"
MIN_YYMM = 2601  # 2026-01 이후만
EXTS = (".xlsx", ".xlsm", ".pdf")
REBUILD = "--rebuild" in sys.argv

def month_of_dirname(d):
    m = re.search(r"(\d{2})(0[1-9]|1[0-2])", d)
    if not m:
        return None
    yy, mm = int(m.group(1)), m.group(2)
    if yy < 20 or yy > 60:
        return None
    return f"20{yy:02d}-{mm}", f"{yy:02d}{mm}"

# ── 재구축 모드: 배치 데이터 + 잘못 시드된 별칭 전체 삭제 ──
if REBUILD:
    conn = main.get_conn()
    try:
        conn.execute("DELETE FROM statement_rows WHERE upload_id IN (SELECT id FROM statement_uploads WHERE uploaded_by='batch_2601+')")
        conn.execute("DELETE FROM statement_uploads WHERE uploaded_by='batch_2601+'")
        conn.execute("DELETE FROM statement_item_map")
        conn.commit()
        print("기존 배치 데이터 삭제 완료")
    finally:
        conn.close()

jobs = []
for d in sorted(os.listdir(BASE)):
    p = os.path.join(BASE, d)
    if not os.path.isdir(p):
        continue
    mm = month_of_dirname(d)
    if not mm:
        continue
    month, yymm = mm
    if int(yymm) < MIN_YYMM:
        continue
    for root, dirs, files in os.walk(p):
        for f in files:
            if not f.lower().endswith(EXTS):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, p).replace("\\", "/")
            fname = f"{d}/{rel}"
            jobs.append((fname, full, month))

print(f"대상 파일: {len(jobs)}개")
ok = dup = fail = 0
t0 = time.time()
for i, (fname, full, month) in enumerate(jobs, 1):
    try:
        data = open(full, "rb").read()
    except Exception as e:
        fail += 1
        print(f"[{i}/{len(jobs)}] READ FAIL {fname}: {e}")
        continue
    uid, err, n = main._stmt_import_file(
        fname, data, "", month,
        uploaded_by="batch_2601+",
        seed_alias_notes={"상품명", "유사", "토큰", "토큰+거래처"},
    )
    if err:
        fail += 1
        print(f"[{i}/{len(jobs)}] FAIL {fname}: {err[:120]}")
    elif n == -1:
        dup += 1
    else:
        ok += 1
    if i % 20 == 0 or i == len(jobs):
        el = time.time() - t0
        print(f"[{i}/{len(jobs)}] ok={ok} dup={dup} fail={fail} ({el:.0f}s)", flush=True)

conn = main.get_conn()
try:
    na = conn.execute("SELECT COUNT(*) AS c FROM statement_item_map").fetchone()["c"]
    nu = conn.execute("SELECT COUNT(*) AS c FROM statement_uploads").fetchone()["c"]
    nr = conn.execute("SELECT COUNT(*) AS c FROM statement_rows").fetchone()["c"]
    print(f"\n완료: uploads={nu} rows={nr} aliases={na} | ok={ok} dup={dup} fail={fail} | {time.time()-t0:.0f}s")
finally:
    conn.close()
