# product_master.category 일괄 UPDATE 실행 스크립트 (운영 DB 직접 실행 — 1회용)
# 사용: python apply_category_update.py
# product_master_category_update.sql 파일의 UPDATE 문을 그대로 실행하고 행 수를 출력한다.
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env")
import db

SQL_FILE = "product_master_category_update.sql"


def main():
    raw = open(SQL_FILE, encoding="utf-8").read()
    lines = [l for l in raw.splitlines() if not l.strip().startswith("--")]
    sql = "\n".join(lines).strip()
    if not sql.upper().startswith("UPDATE"):
        raise SystemExit("UPDATE 문을 찾지 못했습니다. SQL 파일을 확인하세요.")

    conn = db.get_conn()
    try:
        before = conn.execute(
            "SELECT COUNT(*) c FROM product_master WHERE category IS NOT NULL AND btrim(category)<>''"
        ).fetchone()["c"]
        print("before filled:", before)

        cur = conn.execute(sql)
        conn.commit()
        print("update rowcount:", cur.rowcount)

        after = conn.execute(
            "SELECT COUNT(*) c FROM product_master WHERE category IS NOT NULL AND btrim(category)<>''"
        ).fetchone()["c"]
        print("after filled:", after)
        for r in conn.execute(
            "SELECT category, COUNT(*) c FROM product_master "
            "WHERE category IS NOT NULL AND btrim(category)<>'' GROUP BY category ORDER BY c DESC"
        ).fetchall():
            print(" ", r["category"], r["c"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
