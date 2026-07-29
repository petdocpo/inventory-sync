"""
로그인 및 세션 관리 모듈.
지점 계정(자기 지점만 접근) + 마스터 계정(전체 접근) 구분.
SQLite / Supabase(PostgreSQL) 겸용 — db.py의 get_conn() 사용.
지점 목록은 accounts 테이블 기준으로 동적 조회 (마스터가 지점 추가/삭제 가능).
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from db import get_conn, pk_column

# 초기 시딩용 지점 목록 (최초 1회만 사용, 이후로는 accounts 테이블이 정본)
_INITIAL_BRANCHES = [
    {"branch_code": "경기김포점",   "branch_name": "경기 김포점",   "login_id": "경기김포점"},
    {"branch_code": "경기광주점",   "branch_name": "경기 광주점",   "login_id": "경기광주점"},
    {"branch_code": "경기양주점",   "branch_name": "경기 양주점",   "login_id": "경기양주점"},
    {"branch_code": "경기화성1호점", "branch_name": "경기 화성1호점", "login_id": "경기화성1호점"},
    {"branch_code": "경기화성2호점", "branch_name": "경기 화성2호점", "login_id": "경기화성2호점"},
    {"branch_code": "경기용인점",   "branch_name": "경기 용인점",   "login_id": "경기용인점"},
    {"branch_code": "김해점",       "branch_name": "김해점",        "login_id": "김해점"},
    {"branch_code": "경기일산점",   "branch_name": "경기 일산점",   "login_id": "경기일산점"},
    {"branch_code": "부산점",       "branch_name": "부산점",        "login_id": "부산점"},
    {"branch_code": "세종점",       "branch_name": "세종점",        "login_id": "세종점"},
]

DEFAULT_BRANCH_PASSWORD = "1234"
MASTER_ID = "admin_hq"
MASTER_PASSWORD = "1234"


def init_auth_db():
    """계정/세션/자동로그인 토큰 테이블 생성 + 초기 계정 시딩."""
    conn = get_conn()
    pk = pk_column()

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS accounts (
            {pk},
            login_id TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            branch_code TEXT,
            branch_name TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_token TEXT PRIMARY KEY,
            login_id TEXT NOT NULL,
            role TEXT NOT NULL,
            branch_code TEXT,
            expires_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_login_tokens (
            token TEXT PRIMARY KEY,
            branch_code TEXT NOT NULL,
            login_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()

    existing = conn.execute("SELECT id FROM accounts WHERE login_id=?", (MASTER_ID,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO accounts (login_id, password, role, branch_code, branch_name) VALUES (?, ?, 'master', NULL, NULL)",
            (MASTER_ID, MASTER_PASSWORD)
        )

    for b in _INITIAL_BRANCHES:
        existing = conn.execute("SELECT id FROM accounts WHERE login_id=?", (b["login_id"],)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO accounts (login_id, password, role, branch_code, branch_name) VALUES (?, ?, 'branch', ?, ?)",
                (b["login_id"], DEFAULT_BRANCH_PASSWORD, b["branch_code"], b["branch_name"])
            )

    conn.commit()
    conn.close()


def get_branches() -> List[Dict]:
    """현재 등록된 모든 지점 계정 목록을 DB에서 동적으로 조회 (BRANCHES 하드코딩 대체)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT branch_code, branch_name, login_id FROM accounts WHERE role='branch' ORDER BY branch_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_branch(branch_code: str, branch_name: str, login_id: str, password: str) -> Optional[str]:
    """새 지점 계정 추가. 성공 시 None, 실패 시 에러 메시지 반환."""
    conn = get_conn()
    existing = conn.execute("SELECT id FROM accounts WHERE login_id=?", (login_id,)).fetchone()
    if existing:
        conn.close()
        return "이미 존재하는 로그인 ID입니다."
    conn.execute(
        "INSERT INTO accounts (login_id, password, role, branch_code, branch_name) VALUES (?, ?, 'branch', ?, ?)",
        (login_id, password, branch_code, branch_name)
    )
    conn.commit()
    conn.close()
    return None


def delete_branch(branch_code: str) -> Optional[str]:
    """지점 계정 삭제. 성공 시 None, 실패 시 에러 메시지 반환."""
    conn = get_conn()
    existing = conn.execute("SELECT id FROM accounts WHERE branch_code=? AND role='branch'", (branch_code,)).fetchone()
    if not existing:
        conn.close()
        return "해당 지점 계정을 찾을 수 없습니다."
    conn.execute("DELETE FROM accounts WHERE branch_code=? AND role='branch'", (branch_code,))
    conn.commit()
    conn.close()
    return None


def authenticate(login_id: str, password: str) -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM accounts WHERE login_id=? AND password=?",
        (login_id, password)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_session(login_id: str, role: str, branch_code: Optional[str]) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=7)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (session_token, login_id, role, branch_code, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, login_id, role, branch_code, expires)
    )
    conn.commit()
    conn.close()
    return token


def get_session(token: str) -> Optional[Dict]:
    if not token:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE session_token=?", (token,)).fetchone()
    conn.close()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        return None
    return dict(row)


def delete_session(token: str):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE session_token=?", (token,))
    conn.commit()
    conn.close()


def create_auto_login_token(branch_code: str, login_id: str) -> str:
    token = secrets.token_urlsafe(16)
    conn = get_conn()
    conn.execute(
        "INSERT INTO auto_login_tokens (token, branch_code, login_id, created_at) VALUES (?, ?, ?, ?)",
        (token, branch_code, login_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return token


def get_auto_login_info(token: str) -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM auto_login_tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None