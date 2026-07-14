"""
db.py — SQLite / PostgreSQL(Supabase) 겸용 DB 접근 레이어.

목적:
- main.py의 기존 코드(conn.execute("... WHERE x=?", (v,)).fetchall())를
  거의 그대로 유지하면서 DATABASE_URL 유무에 따라 SQLite 또는
  PostgreSQL로 자동 전환한다.
- SQLite: ? 플레이스홀더, sqlite3.Row(dict처럼 r["col"] 접근 가능)
- PostgreSQL: %s 플레이스홀더가 필요하지만, 여기서 ? -> %s 자동 변환.
  RealDictCursor를 써서 dict처럼 r["col"] 접근 가능하게 통일.
- AUTOINCREMENT / INTEGER PRIMARY KEY 등 스키마 차이는
  init_db() 쪽에서 DB 종류에 따라 분기 처리한다 (main.py에서 처리).
"""
import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


class ConnWrapper:
    """SQLite/PostgreSQL 커넥션을 감싸서 동일한 .execute()/.commit()/.close() 인터페이스 제공.
    execute()는 바로 cursor를 반환하고, 그 cursor에서 fetchall()/fetchone()을 그대로 쓸 수 있다.
    """

    def __init__(self, raw_conn):
        self.raw = raw_conn
        self._cursor = None

    def execute(self, query: str, params=None):
        params = params or []
        if USE_POSTGRES:
            # ? -> %s 변환 (문자열 리터럴 안의 ?는 이 프로젝트 쿼리들에 없으므로 단순 치환으로 충분)
            query = query.replace("?", "%s")
            cur = self.raw.cursor()
            cur.execute(query, params)
            self._cursor = cur
            return cur
        else:
            cur = self.raw.execute(query, params)
            self._cursor = cur
            return cur

    def commit(self):
        self.raw.commit()

    def close(self):
        if USE_POSTGRES and self._cursor:
            self._cursor.close()
        self.raw.close()


def get_conn() -> ConnWrapper:
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return ConnWrapper(raw)
    else:
        db_path = os.getenv("QR_DB_PATH", "./data/qr_inventory.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        return ConnWrapper(raw)


def pk_column() -> str:
    """기본키 컬럼 정의 — DB 종류에 따라 다름."""
    if USE_POSTGRES:
        return "id SERIAL PRIMARY KEY"
    return "id INTEGER PRIMARY KEY AUTOINCREMENT"


def upsert_suffix(conflict_cols: str, update_clause: str) -> str:
    """ON CONFLICT 구문 — SQLite/PostgreSQL 문법이 동일해서 공통 사용 가능."""
    return f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_clause}"