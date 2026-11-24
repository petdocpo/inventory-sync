# R6 — _verify_in/ 의 한서 제공 파일을 읽기 전용으로 분석하는 검증용 로더.
# 운영 DB에 쓰지 않는다. 사용법: python _verify_out/load_verify_files.py
# 필요 패키지: openpyxl (requirements.txt에 이미 있음)
import os, sys, glob
from collections import Counter

IN_DIR = os.path.join(os.path.dirname(__file__), '..', '_verify_in')

def analyze_sales_xlsx(path):
    import openpyxl
    from openpyxl.utils.datetime import from_excel
    from datetime import datetime
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    required = ["판매일시", "지점", "고객명", "상품명", "품번", "수량"]
    col_idx, header_row = {}, 0
    rows, skipped = [], []
    date_formats = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                    "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d")
    for row_no, values in enumerate(ws.iter_rows(values_only=True), start=1):
        if not col_idx:
            if row_no > 5: break
            names = ["" if v is None else str(v).strip() for v in values]
            if all(h in names for h in required):
                col_idx = {h: names.index(h) for h in required}
                header_row = row_no
            continue
        if all(v is None or str(v).strip()=="" for v in values): continue
        def g(h):
            i = col_idx[h]; return values[i] if i < len(values) else None
        dt_val = g("판매일시"); dt = None
        if isinstance(dt_val, datetime): dt = dt_val
        elif isinstance(dt_val,(int,float)):
            try: dt = from_excel(dt_val)
            except Exception: dt = None
        elif dt_val is not None:
            s = str(dt_val).strip()
            for fmt in date_formats:
                try: dt = datetime.strptime(s, fmt); break
                except ValueError: continue
        if not isinstance(dt, datetime):
            skipped.append((row_no, "판매일시 형식 오류")); continue
        qty_t = str(g("수량") or "").replace(",","").strip()
        try: qty = int(float(qty_t))
        except ValueError:
            skipped.append((row_no, f"수량 오류: {qty_t}")); continue
        rows.append({
            "date": dt.strftime("%Y-%m-%d"), "dt": dt.strftime("%Y-%m-%d %H:%M"),
            "branch": str(g("지점") or "").strip(), "customer": str(g("고객명") or "").strip(),
            "item": str(g("상품명") or "").strip(), "code": str(g("품번") or "").strip(),
            "qty": qty})
    wb.close()
    dates = sorted({r["date"] for r in rows})
    coded = [r for r in rows if r["code"]]
    branches = Counter(r["branch"] for r in rows)
    items = Counter(r["item"] for r in rows)
    return {
        "file": os.path.basename(path), "type": "sales_xlsx",
        "header_row": header_row, "total_rows": len(rows), "skipped": len(skipped),
        "date_min": dates[0] if dates else None, "date_max": dates[-1] if dates else None,
        "date_count": len(dates), "with_code": len(coded), "no_code": len(rows)-len(coded),
        "branches": dict(branches), "top_items": items.most_common(10),
        "neg_qty_rows": sum(1 for r in rows if r["qty"]<0),
        "sample_skipped": skipped[:10],
    }

def main():
    if not os.path.isdir(IN_DIR):
        print(f"{IN_DIR} 폴더가 없습니다. 한서 파일 대기 중.")
        return
    files = sorted(glob.glob(os.path.join(IN_DIR, '*')))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print(f"{IN_DIR} 에 파일이 없습니다.")
        return
    import json
    out = []
    for f in files:
        low = f.lower()
        if low.endswith('.xlsx'):
            try:
                # 판매내역인지 구매내역인지 헤더로 추정
                out.append(analyze_sales_xlsx(f))
            except Exception as e:
                out.append({"file": os.path.basename(f), "error": str(e)[:300]})
        else:
            out.append({"file": os.path.basename(f), "note": "미지원 형식(수동 확인 필요)"})
    print(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
