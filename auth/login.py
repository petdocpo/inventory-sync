"""
로그인 및 세션 관리 모듈.
지점 계정(자기 지점만 접근) + 마스터 계정(전체 접근) 구분.
SQLite / Supabase(PostgreSQL) 겸용 — db.py의 get_conn() 사용.
지점 목록은 accounts 테이블 기준으로 동적 조회 (마스터가 지점 추가/삭제 가능).
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from db import get_conn, pk_column, upsert_suffix

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

    # ── 마이그레이션: sessions.branch_type 컬럼이 없으면 추가 ──
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS branch_type TEXT")
        conn.commit()
    except Exception:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN branch_type TEXT")
            conn.commit()
        except Exception:
            pass

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


def get_branches(branch_type: Optional[str] = None, team: Optional[str] = None) -> List[Dict]:
    """현재 등록된 모든 지점 계정 목록을 DB에서 동적으로 조회 (BRANCHES 하드코딩 대체).
    branch_type을 지정하면 'branch'(일반 지점) 또는 'hq'(본사 소속 팀)만 필터링.
    team을 지정하면 해당 팀 소속만 필터링. 지정하지 않으면 기존과 동일하게 전체 반환 (하위 호환)."""
    conn = get_conn()
    query = "SELECT branch_code, branch_name, login_id, password, branch_type, team FROM accounts WHERE role='branch'"
    params = []
    if branch_type:
        query += " AND branch_type=?"
        params.append(branch_type)
    if team:
        query += " AND team=?"
        params.append(team)
    query += " ORDER BY branch_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_branch(branch_code: str, branch_name: str, login_id: str, password: str, branch_type: str = "branch", team: Optional[str] = None) -> Optional[str]:
    """새 지점 계정 추가. branch_type: 'branch'(일반지점) 또는 'hq'(본사팀). team: '1팀'/'2팀'/'3팀'/None. 성공 시 None, 실패 시 에러 메시지 반환."""
    if branch_type == "hq":
        # 본사 계정은 지점코드/지점명을 생략할 수 있음 — login_id로 대체
        branch_code = (branch_code or login_id).strip()
        branch_name = (branch_name or login_id).strip()
    if not branch_code or not login_id:
        return "로그인 ID가 필요합니다."

    conn = get_conn()
    existing = conn.execute("SELECT id FROM accounts WHERE login_id=?", (login_id,)).fetchone()
    if existing:
        conn.close()
        return "이미 존재하는 로그인 ID입니다."
    conn.execute(
        "INSERT INTO accounts (login_id, password, role, branch_code, branch_name, branch_type, team) VALUES (?, ?, 'branch', ?, ?, ?, ?)",
        (login_id, password, branch_code, branch_name, branch_type, team)
    )
    conn.commit()
    conn.close()
    return None


def update_branch_account(branch_code: str, branch_name: str, login_id: str, password: str, branch_type: str, team: Optional[str] = None) -> Optional[str]:
    """기존 지점 계정 정보 수정 (지점명/로그인ID/비밀번호/역할/팀). 성공 시 None, 실패 시 에러 메시지 반환."""
    branch_name = (branch_name or "").strip()
    login_id = (login_id or "").strip()

    if branch_type == "hq":
        # 본사 계정: 지점명이 비어있으면 로그인ID로 자동 대체 (사용자가 실수로 이전 값이 남는 것 방지 위해 매번 재확인)
        if not branch_name:
            branch_name = login_id
    else:
        if not branch_name:
            return "지점명을 입력하세요."

    if not login_id:
        return "로그인 ID를 입력하세요."

    conn = get_conn()
    existing = conn.execute("SELECT id FROM accounts WHERE branch_code=? AND role='branch'", (branch_code,)).fetchone()
    if not existing:
        conn.close()
        return "해당 지점 계정을 찾을 수 없습니다."

    dup = conn.execute(
        "SELECT id FROM accounts WHERE login_id=? AND branch_code!=?", (login_id, branch_code)
    ).fetchone()
    if dup:
        conn.close()
        return "이미 사용 중인 로그인 ID입니다."

    conn.execute(
        "UPDATE accounts SET branch_name=?, login_id=?, password=?, branch_type=?, team=? WHERE branch_code=? AND role='branch'",
        (branch_name, login_id, password, branch_type, team, branch_code)
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


def create_session(login_id: str, role: str, branch_code: Optional[str], device_info: str = "", client_ip: str = "", branch_type: Optional[str] = None) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=7)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (session_token, login_id, role, branch_code, expires_at, branch_type) VALUES (?, ?, ?, ?, ?, ?)",
        (token, login_id, role, branch_code, expires, branch_type)
    )
    conn.execute(
        "INSERT INTO login_history (login_id, role, branch_code, device_info, client_ip, logged_in_at) VALUES (?, ?, ?, ?, ?, ?)",
        (login_id, role, branch_code, device_info, client_ip, datetime.now().isoformat())
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

def has_menu_permission(login_id: str, menu_key: str) -> bool:
    """계정의 특정 메뉴 접근 권한 확인. row가 없으면 기본 허용(True)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT enabled FROM menu_permission WHERE login_id=? AND menu_key=?",
        (login_id, menu_key)
    ).fetchone()
    conn.close()
    if row is None:
        return True
    return bool(row["enabled"])


def get_menu_permissions(login_id: str) -> Dict[str, bool]:
    """계정의 모든 메뉴 권한을 dict로 반환 (꺼진 것만 실제 row가 있고, 나머지는 True로 채움)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT menu_key, enabled FROM menu_permission WHERE login_id=?",
        (login_id,)
    ).fetchall()
    conn.close()
    return {r["menu_key"]: bool(r["enabled"]) for r in rows}


def set_menu_permission(login_id: str, menu_key: str, enabled: bool):
    """계정의 특정 메뉴 권한을 설정 (upsert)."""
    conn = get_conn()
    conn.execute(f"""
        INSERT INTO menu_permission (login_id, menu_key, enabled)
        VALUES (?, ?, ?)
        {upsert_suffix('login_id, menu_key', 'enabled=EXCLUDED.enabled, updated_at=NOW()')}
    """, (login_id, menu_key, enabled))
    conn.commit()
    conn.close()

def get_permission_templates() -> List[Dict]:
    """저장된 모든 권한 템플릿 목록 반환."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, template_name FROM permission_template ORDER BY template_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_template_items(template_id: int) -> Dict[str, bool]:
    """특정 템플릿의 메뉴별 on/off 값을 dict로 반환."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT menu_key, enabled FROM permission_template_item WHERE template_id=?",
        (template_id,)
    ).fetchall()
    conn.close()
    return {r["menu_key"]: bool(r["enabled"]) for r in rows}


def save_permission_template(template_name: str, permissions: Dict[str, bool]) -> Optional[str]:
    """현재 권한 세트를 새 템플릿으로 저장 (이름 중복 시 덮어쓰기). 성공 시 None, 실패 시 에러 메시지."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM permission_template WHERE template_name=?", (template_name,)
    ).fetchone()
    if existing:
        template_id = existing["id"]
        conn.execute("DELETE FROM permission_template_item WHERE template_id=?", (template_id,))
    else:
        conn.execute("INSERT INTO permission_template (template_name) VALUES (?)", (template_name,))
        new_row = conn.execute(
            "SELECT id FROM permission_template WHERE template_name=?", (template_name,)
        ).fetchone()
        template_id = new_row["id"]

    for menu_key, enabled in permissions.items():
        conn.execute(
            "INSERT INTO permission_template_item (template_id, menu_key, enabled) VALUES (?, ?, ?)",
            (template_id, menu_key, enabled)
        )
    conn.commit()
    conn.close()
    return None