"""
재고 관리 시스템 - main.py
FastAPI 기반 / SQLite ↔ Supabase(PostgreSQL) 겸용 / 지점별 로그인
"""
import os
import socket
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, Request, Form, File, UploadFile, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import openpyxl

load_dotenv("config/settings.env.example")
load_dotenv(".env")

from db import get_conn, pk_column  # noqa: E402

SERVER_PORT = int(os.getenv("SERVER_PORT", "28000"))
QR_DIR = "./qr_codes"

app = FastAPI(title="재고 관리 시스템", version="1.2.0")

from auth.login import (  # noqa: E402
    init_auth_db, authenticate, create_session, get_session,
    delete_session, create_auto_login_token, get_auto_login_info, BRANCHES
)
init_auth_db()


# ── 공통 함수 ──────────────────────────────────────────

def init_db():
    conn = get_conn()
    pk = pk_column()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS inventory (
            {pk},
            branch_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_code TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            last_updated TEXT,
            UNIQUE(branch_code, item_code)
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS adjustment_log (
            {pk},
            branch_code TEXT,
            item_name TEXT,
            item_code TEXT,
            delta INTEGER,
            result_quantity INTEGER,
            adjusted_at TEXT
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS items (
            {pk},
            branch_code TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_code TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(branch_code, item_code)
        )
    """)
    
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS raw_inventory (
            {pk},
            branch_code TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_code TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            source TEXT DEFAULT 'branch',
            uploaded_at TEXT,
            UNIQUE(branch_code, item_code, source)
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS qr_init_log (
            {pk},
            branch_code TEXT,
            item_code TEXT,
            init_quantity INTEGER,
            initialized_at TEXT
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS hq_bonus_log (
            {pk},
            branch_code TEXT NOT NULL,
            item_code TEXT NOT NULL,
            last_hq_total INTEGER DEFAULT 0,
            updated_at TEXT,
            UNIQUE(branch_code, item_code)
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS scan_log (
            {pk},
            branch_code TEXT NOT NULL,
            branch_name TEXT,
            item_name TEXT,
            item_code TEXT NOT NULL,
            scan_type TEXT,
            result_quantity INTEGER,
            scanned_at TEXT,
            device_info TEXT,
            client_ip TEXT
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS scan_log_delete_history (
            {pk},
            deleted_by TEXT,
            branch_code TEXT,
            item_name TEXT,
            item_code TEXT,
            scan_type TEXT,
            original_scanned_at TEXT,
            deleted_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ── 공통 UI 컴포넌트 ────────────────────────────────────

def render_page(content: str, user: Optional[Dict] = None, active: str = "") -> str:
    """공통 레이아웃 — 상단 타이틀 + 하단 메뉴 포함"""
    branch_name = user["branch_code"] if user and user["role"] == "branch" else ("마스터" if user else "")
    role_badge = f'<span style="background:#4FC3F7;color:white;padding:2px 10px;border-radius:12px;font-size:12px;margin-left:8px;">{branch_name}</span>' if user else ""

    is_master = user and user.get("role") == "master"
    raw_menu_href = "/master/raw-upload" if is_master else "/raw-branch"
    menus = [
        ("dashboard", "/", "⚠️", "대시보드"),
        ("inventory", "/inventory", "📦", "재고현황"),
        ("qr", "/qr", "📷", "QR생성"),
        ("adjust", "/adjust", "✏️", "수기조정"),
        ("raw-branch", raw_menu_href, "📤", "유비플러스 재고"),
        ("scanlog", "/scan-log", "📜", "스캔이력"),
    ]
    if is_master:
        menus.append(("master", "/master", "⚙️", "마스터"))
    menu_html = ""
    for key, href, icon, label in menus:
        is_active = "background:#1E2761;color:white;" if active == key else "color:#555;"
        menu_html += f"""
        <a href="{href}" style="flex:1;text-align:center;padding:8px 0;
           text-decoration:none;font-size:12px;{is_active}border-radius:8px;">
          <div style="font-size:20px;">{icon}</div>
          <div>{label}</div>
        </a>
        """

    return f"""
    <html><head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>재고 관리 시스템</title>
      <link rel="manifest" href="/manifest.json">
      <meta name="theme-color" content="#1E2761">
      <link rel="apple-touch-icon" href="/icon-192.png">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
      <script>
        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', function() {{
            navigator.serviceWorker.register('/sw.js').catch(function() {{}});
          }});
        }}
      </script>
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #f5f7fa; padding-bottom: 80px; }}
        .topbar {{ background: #1E2761; color: white; padding: 10px 14px;
                  display: flex; justify-content: space-between; align-items: center; }}
        .content {{ max-width: 960px; margin: 0 auto; padding: 10px 14px; }}
        .card {{ background: white; border-radius: 12px; padding: 16px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 16px; }}
        .btn {{ background: #1E2761; color: white; padding: 8px 14px;
               border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }}
        .btn:hover {{ background: #2a3580; }}
        .btn-red {{ background: #EF4444; }}
        input, select {{ width: 100%; padding: 8px; border: 1px solid #ddd;
                        border-radius: 8px; font-size: 14px; margin-top: 4px; }}
        table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
        th {{ background: #1E2761; color: white; padding: 6px 5px; text-align: left; font-size: 12px;
             resize: horizontal; overflow: auto; position: relative;
             min-width: 60px; white-space: nowrap; border-right: 1px solid rgba(255,255,255,0.2); }}
        td {{ padding: 6px 5px; border-bottom: 1px solid #eee; font-size: 12px;
             overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .badge-green {{ background:#D1FAE5;color:#065F46;padding:2px 8px;
                       border-radius:10px;font-size:12px; }}
        .badge-red {{ background:#FEE2E2;color:#991B1B;padding:2px 8px;
                     border-radius:10px;font-size:12px; }}
        .bottomnav {{ position:fixed;bottom:0;left:0;right:0;
                     background:white;border-top:1px solid #eee;
                     display:flex;padding:6px 8px;z-index:100; }}
      </style>
    </head>
    <body>
      <div class="topbar">
        <span style="font-weight:bold;">📦 재고 관리 시스템{role_badge}</span>
        <a href="/logout" style="color:#aaa;font-size:13px;text-decoration:none;">로그아웃</a>
      </div>
      <div class="content">{content}</div>
      <nav class="bottomnav">{menu_html}</nav>
      <script>
      (function() {{
        function saveColumnWidths() {{
          document.querySelectorAll('table').forEach(function(table, tIdx) {{
            var widths = [];
            table.querySelectorAll('th').forEach(function(th) {{
              widths.push(th.offsetWidth);
            }});
            localStorage.setItem('colWidths_' + window.location.pathname + '_' + tIdx, JSON.stringify(widths));
          }});
        }}
        function restoreColumnWidths() {{
          document.querySelectorAll('table').forEach(function(table, tIdx) {{
            var saved = localStorage.getItem('colWidths_' + window.location.pathname + '_' + tIdx);
            if (!saved) return;
            try {{
              var widths = JSON.parse(saved);
              var ths = table.querySelectorAll('th');
              ths.forEach(function(th, i) {{
                if (widths[i]) th.style.width = widths[i] + 'px';
              }});
            }} catch (e) {{}}
          }});
        }}
        restoreColumnWidths();
        document.querySelectorAll('th').forEach(function(th) {{
          var observer = new ResizeObserver(function() {{ saveColumnWidths(); }});
          observer.observe(th);
        }});
      }})();
      </script>
    </body></html>
    """


# ── 로그인 ──────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(content="""
    <html><head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>로그인</title>
      <style>
        * { box-sizing:border-box; }
        body { font-family:-apple-system,sans-serif;background:#f5f7fa;
               display:flex;justify-content:center;align-items:center;
               height:100vh;margin:0; }
        .card { background:white;padding:32px;border-radius:16px;
                box-shadow:0 2px 12px rgba(0,0,0,0.1);width:320px; }
        input { width:100%;padding:12px;border:1px solid #ddd;
                border-radius:8px;font-size:14px;margin-top:4px;margin-bottom:14px; }
        .btn { width:100%;background:#1E2761;color:white;padding:13px;
               border:none;border-radius:8px;cursor:pointer;font-size:15px; }
      </style>
    </head>
    <body>
      <div class="card">
        <h2 style="color:#1E2761;text-align:center;margin-bottom:24px;">📦 재고 관리 시스템</h2>
        <form method="post" action="/login">
          <label style="font-size:13px;color:#555;">아이디</label>
          <input name="login_id" required placeholder="아이디 입력">
          <label style="font-size:13px;color:#555;">비밀번호</label>
          <input name="password" type="password" required placeholder="비밀번호 입력">
          <button class="btn" type="submit">로그인</button>
        </form>
      </div>
    </body></html>
    """)


@app.post("/login")
async def login_submit(login_id: str = Form(...), password: str = Form(...)):
    account = authenticate(login_id, password)
    if not account:
        return HTMLResponse(content="""
        <html><head><meta charset="utf-8"></head>
        <body style="font-family:sans-serif;text-align:center;padding-top:80px;">
          <h3>❌ 아이디 또는 비밀번호가 틀렸습니다.</h3>
          <a href="/login">다시 시도</a>
        </body></html>""", status_code=401)
    token = create_session(account["login_id"], account["role"], account["branch_code"])
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="session_token", value=token, max_age=7 * 24 * 3600, httponly=True)
    return resp


@app.get("/logout")
async def logout(session_token: str = Cookie(default=None)):
    if session_token:
        delete_session(session_token)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp


@app.get("/auto-login/{token}")
async def auto_login(token: str):
    info = get_auto_login_info(token)
    if not info:
        return HTMLResponse(content="<h3>❌ 유효하지 않은 자동로그인 링크입니다.</h3>", status_code=404)
    session_token = create_session(info["login_id"], "branch", info["branch_code"])
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="session_token", value=session_token, max_age=7 * 24 * 3600, httponly=True)
    return resp


# ── RAW 목 데이터 (추후 MSSQL 교체) ─────────────────────

def fetch_raw_inventory() -> List[Dict[str, Any]]:
    """RAW 재고 — master 소스 우선, 없으면 branch 소스 사용"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM raw_inventory ORDER BY source DESC").fetchall()
    conn.close()
    merged = {}
    for r in rows:
        key = f"{r['branch_code']}|{r['item_code']}"
        if key not in merged:
            merged[key] = dict(r)
    return list(merged.values())


# ── 대시보드 ────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    session_token: str = Cookie(default=None),
    filter_branch: str = ""
):
    """대시보드 — 불일치 품목만 표시"""
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    if user["role"] == "master":
        if filter_branch:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE branch_code=? ORDER BY item_code",
                (filter_branch,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM inventory ORDER BY branch_code, item_code"
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM inventory WHERE branch_code=? ORDER BY item_code",
            (user["branch_code"],)
        ).fetchall()
    conn.close()

    raw_mock = fetch_raw_inventory()
    raw_map = {f"{r['branch_code']}|{r['item_code']}": r["quantity"] for r in raw_mock}

    disc_rows = ""
    disc_count = 0
    for r in rows:
        key = f"{r['branch_code']}|{r['item_code']}"
        raw_qty = raw_map.get(key, 0)
        diff = r["quantity"] - raw_qty
        if diff != 0:
            disc_count += 1
            color = "#EF4444" if diff < 0 else "#F59E0B"
            disc_rows += f"""
            <tr>
              <td>{r['branch_code']}</td>
              <td>{r['item_name']}</td>
              <td>{r['item_code']}</td>
              <td>{r['quantity']}</td>
              <td>{raw_qty}</td>
              <td style="color:{color};font-weight:bold;">{diff:+d}</td>
              <td>
                <a href="/adjust?preset_branch={r['branch_code']}&preset_code={r['item_code']}"
                   style="background:#1E2761;color:white;padding:4px 10px;
                          border-radius:6px;font-size:12px;text-decoration:none;">
                  수정
                </a>
              </td>
            </tr>"""

    if not disc_rows:
        disc_rows = '<tr><td colspan="7" style="text-align:center;padding:24px;color:#22C55E;">✅ 모든 재고가 일치합니다</td></tr>'

    branch_filter_html = ""
    if user["role"] == "master":
        branch_options = '<option value="">전체 지점</option>'
        for b in BRANCHES:
            sel = "selected" if filter_branch == b["branch_code"] else ""
            branch_options += f'<option value="{b["branch_code"]}" {sel}>{b["branch_name"]}</option>'
        branch_filter_html = f"""
        <form method="get" action="/" style="margin-bottom:16px;">
          <div style="display:flex;gap:8px;align-items:flex-end;">
            <div style="flex:1;max-width:220px;">
              <label style="font-size:12px;color:#888;">지점 필터</label>
              <select name="filter_branch" style="margin-top:4px;">{branch_options}</select>
            </div>
            <button class="btn" type="submit">선택</button>
            <a href="/" style="padding:10px 14px;background:#eee;
               border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
          </div>
        </form>"""

    content = f"""
    <h2 style="margin-bottom:8px;">⚠️ 대시보드</h2>
    <p style="color:#888;font-size:13px;margin-bottom:16px;">불일치 품목만 표시됩니다</p>
    {branch_filter_html}
    <div style="display:flex;gap:12px;margin-bottom:16px;">
      <div class="card" style="flex:1;text-align:center;padding:16px;">
        <div style="color:#888;font-size:12px;">불일치 품목</div>
        <div style="font-size:28px;font-weight:bold;color:#EF4444;">{disc_count}</div>
      </div>
      <div class="card" style="flex:1;text-align:center;padding:16px;">
        <div style="color:#888;font-size:12px;">마지막 확인</div>
        <div style="font-size:13px;font-weight:bold;color:#1E2761;">
          {datetime.now().strftime('%m/%d %H:%M')}
        </div>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr>
          <th>지점</th><th>상품명</th><th>품번</th>
          <th>QR재고</th><th>RAW재고</th><th>차이</th><th>수정</th>
        </tr></thead>
        <tbody>{disc_rows}</tbody>
      </table>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "dashboard"))


# ── 재고현황 ────────────────────────────────────────────

@app.get("/inventory", response_class=HTMLResponse)
async def inventory_page(
    session_token: str = Cookie(default=None),
    search: str = "",
    filter_branch: str = "",
    filter_item: str = ""
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    query = "SELECT * FROM inventory WHERE 1=1"
    params: list = []

    if user["role"] == "branch":
        query += " AND branch_code=?"
        params.append(user["branch_code"])
    elif filter_branch:
        query += " AND branch_code=?"
        params.append(filter_branch)

    if filter_item:
        query += " AND item_code=?"
        params.append(filter_item)

    if search:
        query += " AND (item_name LIKE ? OR item_code LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    query += " ORDER BY branch_code, item_code"
    rows = conn.execute(query, params).fetchall()

    all_branches = conn.execute(
        "SELECT DISTINCT branch_code FROM inventory ORDER BY branch_code"
    ).fetchall() if user["role"] == "master" else []
    all_items = conn.execute(
        "SELECT DISTINCT item_code, item_name FROM inventory ORDER BY item_name"
    ).fetchall()
    conn.close()

    raw_mock = fetch_raw_inventory()
    raw_map = {f"{r['branch_code']}|{r['item_code']}": r["quantity"] for r in raw_mock}

    default_branch_sel = "selected" if not filter_branch else ""
    branch_options = f'<option value="" {default_branch_sel}>전체 지점</option>'
    for b in all_branches:
      sel = "selected" if filter_branch == b["branch_code"] else ""
      branch_options += f'<option value="{b["branch_code"]}" {sel}>{b["branch_code"]}</option>'

    default_sel = "selected" if not filter_item else ""
    item_options = f'<option value="" {default_sel}>전체 품목</option>'
    for it in all_items:
      sel = "selected" if filter_item == it["item_code"] else ""
      item_options += f'<option value="{it["item_code"]}" {sel}>{it["item_name"]}</option>'

    rows_html = ""
    if not rows:
        rows_html = '<tr><td colspan="8" style="text-align:center;padding:24px;color:#888;">데이터 없음</td></tr>'
    else:
        for r in rows:
            key = f"{r['branch_code']}|{r['item_code']}"
            raw_qty = raw_map.get(key, "-")
            diff = (r["quantity"] - raw_qty) if isinstance(raw_qty, int) else "-"
            if diff == "-":
                badge = '<span class="badge-red">RAW없음</span>'
            elif diff == 0:
                badge = '<span class="badge-green">일치</span>'
            else:
                badge = f'<span class="badge-red">불일치 ({diff:+d})</span>'
            rows_html += f"""
            <tr>
              <td style="text-align:center;">
                <input type="checkbox" name="selected_ids" value="{r['id']}"
                       class="inv-check" style="width:16px;height:16px;">
              </td>
              <td>{r['branch_code']}</td>
              <td>{r['item_name']}</td>
              <td>{r['item_code']}</td>
              <td>{r['quantity']}</td>
              <td>{raw_qty}</td>
              <td>{diff if diff != '-' else '-'}</td>
              <td>{badge}</td>
            </tr>"""

    branch_filter_html = ""
    if user["role"] == "master":
        branch_filter_html = f"""
        <div style="flex:1;min-width:120px;">
          <label style="font-size:12px;color:#888;">지점 필터</label>
          <select name="filter_branch" style="margin-top:4px;">{branch_options}</select>
        </div>"""

    content = f"""
    <h2 style="margin-bottom:8px;">📦 재고현황</h2>
    <p style="color:#888;font-size:13px;margin-bottom:16px;">전체 재고를 표시합니다</p>
    <div class="card">
      <form method="get" action="/inventory"
            style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;">
        {branch_filter_html}
        <div style="flex:1;min-width:120px;">
          <label style="font-size:12px;color:#888;">품목 필터</label>
          <select name="filter_item" style="margin-top:4px;">{item_options}</select>
        </div>
        <div style="flex:2;min-width:160px;">
          <label style="font-size:12px;color:#888;">검색 (상품명/품번)</label>
          <input name="search" value="{search}" placeholder="검색어 입력"
                 style="margin-top:4px;">
        </div>
        <button class="btn" type="submit">검색</button>
        <a href="/inventory" style="padding:10px 14px;background:#eee;
           border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
      </form>
    </div>
    <div class="card">
      <form method="post" action="/inventory/delete-selected" id="invForm">
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;color:#888;">{len(rows)}개 항목</span>
          <div style="display:flex;gap:8px;">
            <button type="button" class="btn" id="invSelectAllBtn"
                    style="background:#64748B;font-size:12px;padding:6px 12px;">전체선택</button>
            <button type="submit" class="btn btn-red"
                    style="font-size:12px;padding:6px 12px;"
                    onclick="return confirm('선택한 재고를 삭제할까요?')">선택삭제</button>
          </div>
        </div>
        <table>
          <thead><tr>
            <th style="width:40px;text-align:center;">
              <input type="checkbox" id="invAllCheck" style="width:16px;height:16px;">
            </th>
            <th>지점</th><th>상품명</th><th>품번</th>
            <th>QR재고</th><th>RAW재고</th><th>차이</th><th>상태</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </form>
    </div>
    <script>
      (function() {{
        var allCheck = document.getElementById('invAllCheck');
        var selectBtn = document.getElementById('invSelectAllBtn');
        function applyAll(checked) {{
          document.querySelectorAll('.inv-check').forEach(function(c) {{ c.checked = checked; }});
          if (allCheck) allCheck.checked = checked;
        }}
        if (allCheck) {{
          allCheck.addEventListener('click', function() {{ applyAll(allCheck.checked); }});
        }}
        if (selectBtn) {{
          selectBtn.addEventListener('click', function() {{
            var next = !(allCheck && allCheck.checked);
            applyAll(next);
          }});
        }}
      }})();
    </script>
    """
    return HTMLResponse(content=render_page(content, user, "inventory"))


@app.post("/inventory/delete-selected")
async def inventory_delete_selected(
    request: Request,
    session_token: str = Cookie(default=None)
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ids = form.getlist("selected_ids")
    if ids:
        conn = get_conn()
        if user["role"] == "master":
            conn.execute(
                f"DELETE FROM inventory WHERE id IN ({','.join('?' for _ in ids)})",
                [int(i) for i in ids]
            )
        else:
            conn.execute(
                f"DELETE FROM inventory WHERE id IN ({','.join('?' for _ in ids)}) AND branch_code=?",
                [int(i) for i in ids] + [user["branch_code"]]
            )
        conn.commit()
        conn.close()
    return RedirectResponse(url="/inventory", status_code=303)


# ── 데이터 관리 (마스터 전용 - 구 경로, 호환용 리다이렉트) ─

@app.get("/data")
async def data_page_redirect():
    return RedirectResponse(url="/master/data", status_code=303)


# ── QR 생성 ─────────────────────────────────────────────

def generate_qr_bytes(server_url, branch_code, item_code, scan_type, item_name="") -> bytes:
    """QR 코드를 메모리에서 생성 + 하단에 상품명/입출고 라벨 삽입"""
    import qrcode
    import io
    from PIL import Image, ImageDraw, ImageFont

    url = (f"{server_url}/scan"
           f"?branch_code={branch_code}"
           f"&item_code={item_code}"
           f"&scan_type={scan_type}")
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    label_height = int(qr_img.height * 0.35)
    canvas = Image.new("RGB", (qr_img.width, qr_img.height + label_height), "white")
    canvas.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(canvas)
    try:
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic-Bold.ttf")
        font_size = int(qr_img.height * 0.09)
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    type_label = "입고 IN" if scan_type == "IN" else "출고 OUT"
    text1 = item_name[:20] if item_name else item_code
    text2 = type_label

    for i, text in enumerate([text1, text2]):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        x = (canvas.width - w) / 2
        y = qr_img.height + int(label_height * 0.15) + i * int(font_size * 1.3)
        draw.text((x, y), text, fill="black", font=font)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


@app.get("/qr/download/{filename}")
async def qr_download(filename: str):
    """QR 이미지를 즉석에서 생성해 반환 (파일 저장 없이 동작, Vercel 호환)"""
    from fastapi.responses import Response
    name = filename.replace(".png", "")
    parts = name.rsplit("_", 1)
    if len(parts) != 2:
        return Response(content=b"Invalid filename format", status_code=404)
    prefix, scan_type = parts
    branch_part = prefix.split("_", 1)
    if len(branch_part) != 2:
        return Response(content=b"Invalid filename format", status_code=404)
    branch_code, item_code = branch_part

    hostname_env = os.getenv("PUBLIC_SERVER_URL")
    if hostname_env:
        server_url = hostname_env
    else:
        hostname = socket.gethostbyname(socket.gethostname())
        server_url = f"http://{hostname}:{SERVER_PORT}"

    item_name = ""
    try:
        conn = get_conn()
        item = conn.execute(
            "SELECT item_name FROM items WHERE branch_code=? AND item_code=?",
            (branch_code, item_code)
        ).fetchone()
        conn.close()
        item_name = item["item_name"] if item else ""
    except Exception:
        item_name = ""

    try:
        img_bytes = generate_qr_bytes(server_url, branch_code, item_code, scan_type, item_name)
    except Exception as e:
        return Response(content=f"QR generation failed: {str(e)}".encode(), status_code=500)

    return Response(content=img_bytes, media_type="image/png")

def generate_qr_image(server_url, branch_code, item_name, item_code, scan_type, output_dir=QR_DIR):
    """QR 코드 생성 — 로컬 환경에서는 파일로도 저장 (호환용), Vercel에서는 파일 저장 실패해도 무시"""
    filename = f"{branch_code}_{item_code}_{scan_type}.png"
    conn = get_conn()
    item = conn.execute(
        "SELECT item_name FROM items WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    conn.close()
    item_name = item["item_name"] if item else ""
    img_bytes = generate_qr_bytes(server_url, branch_code, item_code, scan_type, item_name)
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        file_path = Path(output_dir) / filename
        with open(file_path, "wb") as f:
            f.write(img_bytes)
        return str(file_path), filename
    except Exception:
        # Vercel 등 읽기전용 파일시스템에서는 파일 저장 생략, 바이트만 사용
        return None, filename


@app.get("/qr", response_class=HTMLResponse)
async def qr_page(
    session_token: str = Cookie(default=None),
    filter_branch: str = ""
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if user["role"] == "branch":
        filter_branch = user["branch_code"]

    conn = get_conn()
    if filter_branch:
        items = conn.execute(
            "SELECT * FROM items WHERE branch_code=? ORDER BY item_name",
            (filter_branch,)
        ).fetchall()
    else:
        items = conn.execute(
            "SELECT * FROM items ORDER BY branch_code, item_name"
        ).fetchall()
    conn.close()

    options_html = '<option value="">-- 품목 선택 --</option>'
    for it in items:
        options_html += f'<option value="{it["branch_code"]}|{it["item_name"]}|{it["item_code"]}">{it["branch_name"]} / {it["item_name"]} ({it["item_code"]})</option>'

    branch_filter_html = ""
    bulk_html = ""

    if user["role"] == "master":
        branch_options = '<option value="">전체 지점</option>'
        for b in BRANCHES:
            sel = "selected" if filter_branch == b["branch_code"] else ""
            branch_options += f'<option value="{b["branch_code"]}" {sel}>{b["branch_name"]}</option>'

        branch_filter_html = f"""
        <form method="get" action="/qr" style="margin-bottom:12px;">
          <div style="display:flex;gap:8px;align-items:flex-end;">
            <div style="flex:1;">
              <label style="font-size:12px;color:#888;">지점 선택</label>
              <select name="filter_branch" style="margin-top:4px;">{branch_options}</select>
            </div>
            <button class="btn" type="submit">선택</button>
            <a href="/qr" style="padding:10px 14px;background:#eee;
               border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
          </div>
        </form>"""

        if filter_branch:
            bulk_html = f"""
            <div class="card" style="border:1px solid #1E2761;">
              <h3 style="margin-bottom:8px;">📦 일괄 생성</h3>
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <form method="post" action="/master/qr/generate-bulk" style="flex:1;" onsubmit="showZipLoading();">
                  <input type="hidden" name="branch_code" value="{filter_branch}">
                  <button class="btn" type="submit" style="width:100%;">
                    📦 {filter_branch} ZIP 다운로드
                  </button>
                </form>
                <form method="post" action="/master/qr/generate-bulk" style="flex:1;" onsubmit="showZipLoading();">
                  <input type="hidden" name="branch_code" value="ALL">
                  <button class="btn" type="submit"
                          style="width:100%;background:#64748B;">
                    🌐 전체 지점 ZIP 다운로드
                  </button>
                </form>
              </div>
            </div>"""
        else:
            bulk_html = """
            <div class="card" style="border:1px solid #1E2761;">
              <h3 style="margin-bottom:8px;">📦 일괄 생성</h3>
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <form method="post" action="/master/qr/generate-bulk" style="flex:1;" onsubmit="showZipLoading();">
                  <input type="hidden" name="branch_code" value="ALL">
                  <button class="btn" type="submit" style="width:100%;">
                    🌐 전체 지점 ZIP 다운로드
                  </button>
                </form>
              </div>
              <p style="color:#aaa;font-size:12px;margin-top:8px;">
                특정 지점만 생성하려면 위에서 지점을 선택하세요.
              </p>
            </div>"""
    else:
        # 지점 계정 — 본인 지점 일괄생성 버튼 항상 표시
        bulk_html = f"""
        <div class="card" style="border:1px solid #1E2761;">
          <h3 style="margin-bottom:8px;">📦 일괄 생성</h3>
          <p style="color:#666;font-size:12px;margin-bottom:12px;">
            우리 지점({user['branch_code']})의 전체 품목 QR을 ZIP으로 한 번에 받을 수 있어요.
          </p>
          <form method="post" action="/master/qr/generate-bulk" onsubmit="showZipLoading();">
            <input type="hidden" name="branch_code" value="{user['branch_code']}">
            <button class="btn" type="submit" style="width:100%;">
              📦 우리 지점 전체 QR ZIP 다운로드
            </button>
          </form>
        </div>"""

    content = f"""
    <h2 style="margin-bottom:16px;">📷 QR / 바코드 생성</h2>
    {branch_filter_html}
    <div class="card">
      <h3 style="margin-bottom:12px;">개별 생성</h3>
      <form method="post" action="/qr/generate" id="qrGenForm"
            onsubmit="startQrGenTimer();">
        <label style="font-size:13px;color:#555;">품목 선택 ({len(items)}개)</label>
        <select name="item_key" required style="margin-bottom:14px;">
          {options_html}
        </select>
        <button class="btn" type="submit" id="qrGenBtn" style="width:100%;">입고/출고 생성</button>
      </form>
    </div>
    {bulk_html}

    <div id="zipLoadingOverlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;
         background:rgba(0,0,0,0.5);z-index:999;justify-content:center;align-items:center;">
      <div style="background:white;padding:24px 32px;border-radius:12px;text-align:center;">
        <div style="font-size:32px;margin-bottom:8px;">📦</div>
        <div style="font-weight:bold;color:#1E2761;">ZIP 생성 중입니다...</div>
        <div id="zipTimerText" style="color:#888;font-size:13px;margin-top:6px;">잠시만 기다려주세요</div>
      </div>
    </div>
    <script>
    function startQrGenTimer() {{
      const btn = document.getElementById('qrGenBtn');
      let seconds = 0;
      btn.disabled = true;
      const interval = setInterval(() => {{
        seconds++;
        btn.innerHTML = `⏳ 생성 중... (${{seconds}}초째)`;
      }}, 1000);
      btn.innerHTML = '⏳ 생성 중... (0초째)';
    }}
    function showZipLoading() {{
      document.getElementById('zipLoadingOverlay').style.display = 'flex';
      let seconds = 0;
      const label = document.getElementById('zipTimerText');
      const interval = setInterval(() => {{
        seconds++;
        label.textContent = `${{seconds}}초째 생성 중...`;
      }}, 1000);
      setTimeout(() => {{
        document.getElementById('zipLoadingOverlay').style.display = 'none';
        clearInterval(interval);
      }}, 60000);
    }}
    </script>
    """
    return HTMLResponse(content=render_page(content, user, "qr"))


@app.post("/qr/generate", response_class=HTMLResponse)
async def qr_generate(
    session_token: str = Cookie(default=None),
    item_key: str = Form(...)
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    parts = item_key.split("|")
    if len(parts) != 3:
        return HTMLResponse(content=render_page('<div class="card"><p>❌ 품목 선택 오류</p></div>', user, "qr"))

    branch_code, item_name, item_code = parts
    hostname_env = os.getenv("PUBLIC_SERVER_URL")
    if hostname_env:
        server_url = hostname_env
    else:
        hostname = socket.gethostbyname(socket.gethostname())
        server_url = f"http://{hostname}:{SERVER_PORT}"

    _, in_file = generate_qr_image(server_url, branch_code, item_name, item_code, "IN")
    _, out_file = generate_qr_image(server_url, branch_code, item_name, item_code, "OUT")

    content = f"""
    <h2 style="margin-bottom:16px;">✅ QR 생성 완료</h2>
    <div class="card">
      <p><b>지점:</b> {branch_code}</p>
      <p><b>상품명:</b> {item_name}</p>
      <p style="margin-bottom:16px;"><b>품번:</b> {item_code}</p>
      <div style="display:flex;gap:16px;flex-wrap:wrap;">
        <div style="text-align:center;flex:1;">
          <p style="font-weight:bold;color:#22C55E;margin-bottom:8px;">📥 입고 QR</p>
          <img src="/qr/download/{in_file}" style="width:160px;height:160px;border:1px solid #eee;border-radius:8px;"><br>
          <a href="/qr/download/{in_file}" download
             style="display:inline-block;margin-top:8px;background:#1E2761;
                    color:white;padding:8px 16px;border-radius:8px;
                    text-decoration:none;font-size:13px;">다운로드</a>
        </div>
        <div style="text-align:center;flex:1;">
          <p style="font-weight:bold;color:#EF4444;margin-bottom:8px;">📤 출고 QR</p>
          <img src="/qr/download/{out_file}" style="width:160px;height:160px;border:1px solid #eee;border-radius:8px;"><br>
          <a href="/qr/download/{out_file}" download
             style="display:inline-block;margin-top:8px;background:#1E2761;
                    color:white;padding:8px 16px;border-radius:8px;
                    text-decoration:none;font-size:13px;">다운로드</a>
        </div>
      </div>
      <div style="margin-top:16px;">
        <a href="/qr" style="color:#1E2761;font-size:13px;">← QR 생성으로 돌아가기</a>
      </div>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "qr"))


# ── QR 스캔 / 재고 조정 로직 ─────────────────────────────

def adjust_quantity(branch_code: str, item_code: str, delta: int, absolute: bool = False) -> int:
    conn = get_conn()
    item = conn.execute(
        "SELECT * FROM items WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    item_name = item["item_name"] if item else item_code
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    now = datetime.now().isoformat()

    if absolute:
        new_qty = max(0, delta)
    else:
        new_qty = max(0, (row["quantity"] if row else 0) + delta)

    if row is None:
        conn.execute(
            "INSERT INTO inventory (branch_code, item_name, item_code, quantity, last_updated) VALUES (?, ?, ?, ?, ?)",
            (branch_code, item_name, item_code, new_qty, now)
        )
    else:
        conn.execute(
            "UPDATE inventory SET quantity=?, item_name=?, last_updated=? WHERE branch_code=? AND item_code=?",
            (new_qty, item_name, now, branch_code, item_code)
        )
    conn.commit()
    conn.close()
    return new_qty


@app.get("/scan", response_class=HTMLResponse)
async def scan_get(request: Request, branch_code: str, item_code: str, scan_type: str):
    delta = 1 if scan_type == "IN" else -1
    new_qty = adjust_quantity(branch_code, item_code, delta)

    device_info = request.headers.get("user-agent", "")[:255]
    client_ip = request.client.host if request.client else ""

    conn = get_conn()
    item = conn.execute(
        "SELECT item_name, branch_name FROM items WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    item_name = item["item_name"] if item else item_code
    branch_name = item["branch_name"] if item else branch_code
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO scan_log (branch_code, branch_name, item_name, item_code, scan_type, result_quantity, scanned_at, device_info, client_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (branch_code, branch_name, item_name, item_code, scan_type, new_qty, now, device_info, client_ip)
    )
    conn.commit()
    conn.close()

    action_label = "입고 ✅" if scan_type == "IN" else "출고 📤"
    bg_color = "#D1FAE5" if scan_type == "IN" else "#FEE2E2"
    text_color = "#065F46" if scan_type == "IN" else "#991B1B"

    return HTMLResponse(content=f"""
    <html><head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>스캔 완료</title>
    </head>
    <body style="font-family:-apple-system,sans-serif;background:#f5f7fa;
                 display:flex;justify-content:center;align-items:center;
                 min-height:100vh;margin:0;">
      <div style="background:white;max-width:340px;width:90%;padding:32px;
                  border-radius:20px;box-shadow:0 4px 20px rgba(0,0,0,0.1);
                  text-align:center;">
        <div style="background:{bg_color};border-radius:12px;padding:16px;
                    margin-bottom:20px;">
          <div style="font-size:36px;margin-bottom:4px;">{action_label}</div>
          <div style="font-size:20px;font-weight:bold;color:{text_color};">
            {'입고' if scan_type == 'IN' else '출고'} 처리 완료
          </div>
        </div>
        <div style="text-align:left;background:#f8fafc;border-radius:10px;
                    padding:16px;margin-bottom:16px;">
          <div style="margin-bottom:10px;">
            <div style="font-size:11px;color:#888;margin-bottom:2px;">지점</div>
            <div style="font-size:15px;font-weight:bold;">{branch_name}</div>
          </div>
          <div style="margin-bottom:10px;">
            <div style="font-size:11px;color:#888;margin-bottom:2px;">상품명</div>
            <div style="font-size:15px;font-weight:bold;">{item_name}</div>
          </div>
          <div style="margin-bottom:10px;">
            <div style="font-size:11px;color:#888;margin-bottom:2px;">품번</div>
            <div style="font-size:14px;color:#64748B;">{item_code}</div>
          </div>
          <div>
            <div style="font-size:11px;color:#888;margin-bottom:2px;">현재 재고</div>
            <div style="font-size:28px;font-weight:bold;color:#1E2761;">{new_qty}개</div>
          </div>
        </div>
        <div style="font-size:12px;color:#aaa;">
          {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
      </div>
    </body></html>
    """)


@app.post("/scan")
async def scan_post(data: Dict[str, str]):
    delta = 1 if data.get("scan_type") == "IN" else -1
    new_qty = adjust_quantity(data["branch_code"], data["item_code"], delta)
    return {"status": "scanned", "scan_type": data.get("scan_type"), "new_quantity": new_qty}


# ── 수기 조정 ──────────────────────────────────────────

@app.get("/adjust", response_class=HTMLResponse)
async def adjust_get(
    session_token: str = Cookie(default=None),
    preset_branch: str = "",
    preset_code: str = "",
    filter_branch: str = ""
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    effective_branch = preset_branch or filter_branch

    conn = get_conn()
    if user["role"] == "master":
        if effective_branch:
            items = conn.execute(
                "SELECT * FROM items WHERE branch_code=? ORDER BY item_name",
                (effective_branch,)
            ).fetchall()
            logs = conn.execute(
                "SELECT * FROM adjustment_log WHERE branch_code=? ORDER BY id DESC LIMIT 20",
                (effective_branch,)
            ).fetchall()
        else:
            items = conn.execute(
                "SELECT * FROM items ORDER BY branch_code, item_name"
            ).fetchall()
            logs = conn.execute(
                "SELECT * FROM adjustment_log ORDER BY id DESC LIMIT 20"
            ).fetchall()
    else:
        items = conn.execute(
            "SELECT * FROM items WHERE branch_code=? ORDER BY item_name",
            (user["branch_code"],)
        ).fetchall()
        logs = conn.execute(
            "SELECT * FROM adjustment_log WHERE branch_code=? ORDER BY id DESC LIMIT 20",
            (user["branch_code"],)
        ).fetchall()
    conn.close()

    branch_filter_html = ""
    if user["role"] == "master":
        branch_options = '<option value="">전체 지점</option>'
        for b in BRANCHES:
            sel = "selected" if filter_branch == b["branch_code"] else ""
            branch_options += f'<option value="{b["branch_code"]}" {sel}>{b["branch_name"]}</option>'
        branch_filter_html = f"""
        <div class="card">
          <form method="get" action="/adjust" style="display:flex;gap:8px;align-items:flex-end;">
            <div style="flex:1;max-width:220px;">
              <label style="font-size:12px;color:#888;">지점 필터</label>
              <select name="filter_branch" style="margin-top:4px;">{branch_options}</select>
            </div>
            <button class="btn" type="submit">선택</button>
            <a href="/adjust" style="padding:10px 14px;background:#eee;
               border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
          </form>
        </div>"""

    options_html = ""
    for it in items:
        sel = "selected" if preset_code == it["item_code"] and preset_branch == it["branch_code"] else ""
        options_html += f'<option value="{it["branch_code"]}|{it["item_code"]}" {sel}>{it["branch_name"]} / {it["item_name"]} ({it["item_code"]})</option>'

    log_rows = ""
    if not logs:
        log_rows = '<tr><td colspan="7" style="text-align:center;padding:16px;color:#888;">이력 없음</td></tr>'
    else:
        for lg in logs:
            delta_str = f"+{lg['delta']}" if lg['delta'] > 0 else str(lg['delta'])
            log_rows += f"""
            <tr>
              <td style="text-align:center;">
                <input type="checkbox" name="log_ids" value="{lg['id']}" class="log-check" style="width:16px;height:16px;">
              </td>
              <td>{lg['adjusted_at'][:16] if lg['adjusted_at'] else '-'}</td>
              <td>{lg['branch_code']}</td>
              <td>{lg['item_name'] or '-'}</td>
              <td>{lg['item_code']}</td>
              <td>{delta_str}</td>
              <td>{lg['result_quantity']}</td>
            </tr>"""

    log_controls = """
        <div style="display:flex;gap:8px;">
          <button type="button" class="btn" id="logSelectAllBtn"
                  style="background:#64748B;font-size:12px;padding:6px 12px;">전체선택</button>
          <button type="submit" class="btn btn-red"
                  style="font-size:12px;padding:6px 12px;"
                  onclick="return confirm('선택한 이력을 삭제할까요?')">선택삭제</button>
          <button type="button" class="btn btn-red" id="logDeleteAllBtn"
                  style="font-size:12px;padding:6px 12px;">전체삭제</button>
        </div>"""

    log_header_check = """<th style="width:40px;text-align:center;">
              <input type="checkbox" id="logAllCheck" style="width:16px;height:16px;">
            </th>"""

    log_section = f"""
    <div class="card">
      <form method="post" action="/adjust/delete-logs" id="logForm">
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:12px;">
          <h3>최근 조정 이력</h3>
          {log_controls}
        </div>
        <table>
          <thead><tr>
            {log_header_check}
            <th>시각</th><th>지점</th><th>상품명</th><th>품번</th><th>조정</th><th>결과</th>
          </tr></thead>
          <tbody>{log_rows}</tbody>
        </table>
      </form>
    </div>
    <form method="post" action="/adjust/delete-all-logs" id="logDeleteAllForm"></form>
    <script>
      (function() {{
        var allCheck = document.getElementById('logAllCheck');
        var selectBtn = document.getElementById('logSelectAllBtn');
        var deleteAllBtn = document.getElementById('logDeleteAllBtn');

        function applyAll(checked) {{
          document.querySelectorAll('.log-check').forEach(function(c) {{ c.checked = checked; }});
          if (allCheck) allCheck.checked = checked;
        }}

        if (allCheck) {{
          allCheck.addEventListener('click', function() {{ applyAll(allCheck.checked); }});
        }}
        if (selectBtn) {{
          selectBtn.addEventListener('click', function() {{
            var next = !(allCheck && allCheck.checked);
            applyAll(next);
          }});
        }}
        if (deleteAllBtn) {{
          deleteAllBtn.addEventListener('click', function() {{
            if (confirm('전체 이력을 삭제합니다.')) {{
              document.getElementById('logDeleteAllForm').submit();
            }}
          }});
        }}
      }})();
    </script>
    """

    content = f"""
    <h2 style="margin-bottom:16px;">✏️ 수기 조정</h2>
    {branch_filter_html}
    <div class="card">
      <div style="margin-bottom:12px;">
        <label style="font-size:13px;color:#555;">품목 검색</label>
        <input id="itemSearch" placeholder="상품명 또는 품번 입력"
               oninput="filterItems()"
               style="margin-top:4px;">
      </div>
      <form method="post" action="/adjust">
        <label style="font-size:13px;color:#555;">품목 선택</label>
        <select name="item_key" id="itemSelect" required
                style="margin-bottom:14px;" onchange="loadQty(this)">
          <option value="">-- 품목 선택 --</option>
          {options_html}
        </select>

        <div id="qtyInfo" style="display:none;background:#f0f4ff;border-radius:8px;
             padding:12px;margin-bottom:14px;">
          <div style="display:flex;gap:16px;">
            <div>
              <div style="font-size:11px;color:#888;">QR 재고</div>
              <div id="qrQty" style="font-size:20px;font-weight:bold;color:#1E2761;">-</div>
            </div>
            <div>
              <div style="font-size:11px;color:#888;">RAW 재고</div>
              <div id="rawQty" style="font-size:20px;font-weight:bold;color:#64748B;">-</div>
            </div>
            <div>
              <div style="font-size:11px;color:#888;">차이</div>
              <div id="diffQty" style="font-size:20px;font-weight:bold;color:#EF4444;">-</div>
            </div>
          </div>
        </div>

        <label style="font-size:13px;color:#555;">현재 재고 수량으로 설정 (입력값이 곧 현재 재고)</label>
        <input name="delta" type="number" required placeholder="예: 5 또는 100"
               style="margin-bottom:14px;">
        <button class="btn" type="submit" style="width:100%;">조정 적용</button>
      </form>
    </div>
    {log_section}

    <script>
    const allOptions = Array.from(document.querySelectorAll('#itemSelect option'));

    function filterItems() {{
      const kw = document.getElementById('itemSearch').value.toLowerCase();
      const sel = document.getElementById('itemSelect');
      sel.innerHTML = '';
      allOptions.forEach(opt => {{
        if (!opt.value || opt.text.toLowerCase().includes(kw)) {{
          sel.appendChild(opt.cloneNode(true));
        }}
      }});
    }}

    function loadQty(sel) {{
      const val = sel.value;
      if (!val) {{
        document.getElementById('qtyInfo').style.display = 'none';
        return;
      }}
      const [branch, code] = val.split('|');
      fetch(`/api/qty?branch_code=${{branch}}&item_code=${{code}}`)
        .then(r => r.json())
        .then(d => {{
          document.getElementById('qrQty').textContent = d.qr_qty;
          document.getElementById('rawQty').textContent = d.raw_qty ?? '-';
          const diff = (d.raw_qty !== null) ? d.qr_qty - d.raw_qty : null;
          document.getElementById('diffQty').textContent = diff !== null
            ? (diff >= 0 ? '+' : '') + diff : '-';
          document.getElementById('qtyInfo').style.display = 'block';
        }});
    }}

    window.onload = function() {{
      const sel = document.getElementById('itemSelect');
      if (sel.value) loadQty(sel);
    }};
    </script>
    """
    return HTMLResponse(content=render_page(content, user, "adjust"))


@app.post("/adjust")
async def adjust_post(
    session_token: str = Cookie(default=None),
    item_key: str = Form(...),
    delta: int = Form(...)
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    parts = item_key.split("|")
    if len(parts) != 2:
        return RedirectResponse(url="/adjust", status_code=303)
    branch_code, item_code = parts
    new_qty = adjust_quantity(branch_code, item_code, delta, absolute=True)
    conn = get_conn()
    item = conn.execute(
        "SELECT item_name FROM items WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    item_name = item["item_name"] if item else item_code
    conn.execute(
        "INSERT INTO adjustment_log (branch_code, item_name, item_code, delta, result_quantity, adjusted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (branch_code, item_name, item_code, delta, new_qty, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/adjust", status_code=303)


@app.post("/adjust/delete-logs")
async def adjust_delete_logs(
    request: Request,
    session_token: str = Cookie(default=None)
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ids = form.getlist("log_ids")
    if ids:
        conn = get_conn()
        if user["role"] == "master":
            conn.execute(
                f"DELETE FROM adjustment_log WHERE id IN ({','.join('?' for _ in ids)})",
                [int(i) for i in ids]
            )
        else:
            conn.execute(
                f"DELETE FROM adjustment_log WHERE id IN ({','.join('?' for _ in ids)}) AND branch_code=?",
                [int(i) for i in ids] + [user["branch_code"]]
            )
        conn.commit()
        conn.close()
    return RedirectResponse(url="/adjust", status_code=303)


@app.post("/adjust/delete-all-logs")
async def adjust_delete_all_logs(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/adjust", status_code=303)
    conn = get_conn()
    if user["role"] == "master":
        conn.execute("DELETE FROM adjustment_log")
    else:
        conn.execute("DELETE FROM adjustment_log WHERE branch_code=?", (user["branch_code"],))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/adjust", status_code=303)


@app.get("/api/qty")
async def api_qty(branch_code: str, item_code: str,
                   session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user:
        return {"qr_qty": 0, "raw_qty": None}
    conn = get_conn()
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE branch_code=? AND item_code=?",
        (branch_code, item_code)
    ).fetchone()
    conn.close()
    qr_qty = row["quantity"] if row else 0
    raw_data = fetch_raw_inventory()
    raw_map = {f"{r['branch_code']}|{r['item_code']}": r["quantity"] for r in raw_data}
    raw_qty = raw_map.get(f"{branch_code}|{item_code}", None)
    return {"qr_qty": qr_qty, "raw_qty": raw_qty}

# ── 스캔 이력 조회 ──────────────────────────────────────

@app.get("/scan-log", response_class=HTMLResponse)
async def scan_log_page(
    session_token: str = Cookie(default=None),
    search: str = "",
    date_filter: str = ""
):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    query = "SELECT * FROM scan_log WHERE 1=1"
    params: list = []
    if user["role"] == "branch":
        query += " AND branch_code=?"
        params.append(user["branch_code"])
    if search:
        query += " AND (item_name LIKE ? OR item_code LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if date_filter:
        query += " AND scanned_at LIKE ?"
        params.append(f"{date_filter}%")
    query += " ORDER BY id DESC LIMIT 200"
    logs = conn.execute(query, params).fetchall()
    conn.close()

    from datetime import timedelta, timezone
    KST = timezone(timedelta(hours=9))

    def to_kst_str(iso_str):
        if not iso_str:
            return "-"
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_kst = dt.astimezone(KST)
            return dt_kst.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return iso_str[:16] if len(iso_str) >= 16 else iso_str

    rows_html = ""
    if not logs:
        rows_html = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#888;">스캔 이력 없음</td></tr>'
    else:
        for lg in logs:
            type_label = "입고" if lg["scan_type"] == "IN" else "출고"
            type_color = "#22C55E" if lg["scan_type"] == "IN" else "#EF4444"
            check_cell = ""
            device_cell = ""
            if user["role"] == "master":
                check_cell = f'<td style="text-align:center;"><input type="checkbox" name="log_ids" value="{lg["id"]}" class="scanlog-check" style="width:16px;height:16px;"></td>'
                device_raw = (lg["device_info"] if "device_info" in lg.keys() else "") or "-"
                ip_raw = (lg["client_ip"] if "client_ip" in lg.keys() else "") or "-"
                device_short = device_raw[:30] + ("..." if len(device_raw) > 30 else "")
                device_cell = f'<td style="font-size:9px;color:#888;" title="{device_raw}">{device_short}<br>{ip_raw}</td>'
            rows_html += f"""
            <tr>
              {check_cell}
              <td>{to_kst_str(lg['scanned_at'])}</td>
              <td>{lg['branch_name'] or lg['branch_code']}</td>
              <td>{lg['item_name'] or '-'}</td>
              <td>{lg['item_code']}</td>
              <td style="color:{type_color};font-weight:bold;">{type_label}</td>
              <td>{lg['result_quantity']}</td>
              {device_cell}
            </tr>"""

    delete_controls = ""
    header_check = ""
    if user["role"] == "master":
        header_check = '<th style="width:40px;text-align:center;"><input type="checkbox" id="scanlogAllCheck" style="width:16px;height:16px;"></th>'
        delete_controls = """
        <div style="display:flex;gap:8px;margin-bottom:12px;">
          <button type="button" class="btn" id="scanlogSelectAllBtn"
                  style="background:#64748B;font-size:12px;padding:6px 12px;">전체선택</button>
          <button type="submit" class="btn btn-red"
                  style="font-size:12px;padding:6px 12px;"
                  onclick="return confirm('선택한 스캔 이력을 삭제할까요?')">선택삭제</button>
          <button type="button" class="btn btn-red" id="scanlogDeleteAllBtn"
                  style="font-size:12px;padding:6px 12px;">전체삭제</button>
        </div>"""

    table_open = '<form method="post" action="/scan-log/delete">' if user["role"] == "master" else '<div>'
    table_close = '</form>' if user["role"] == "master" else '</div>'

    content = f"""
    <h2 style="margin-bottom:16px;">📜 스캔 이력</h2>
    <div class="card">
      <form method="get" action="/scan-log" style="display:flex;gap:8px;flex-wrap:wrap;">
        <input name="search" value="{search}" placeholder="상품명/품번 검색" style="flex:1;min-width:140px;">
        <input name="date_filter" type="date" value="{date_filter}" style="flex:1;min-width:140px;">
        <button class="btn" type="submit">검색</button>
        <a href="/scan-log" style="padding:10px 14px;background:#eee;
           border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
      </form>
    </div>
    <div class="card">
      {table_open}
        {delete_controls}
        <table>
          <thead><tr>
            {header_check}
            <th>시각</th><th>지점</th><th>상품명</th><th>품번</th><th>구분</th><th>처리후 재고</th>{'<th>기기/IP</th>' if user["role"] == "master" else ''}
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      {table_close}
    </div>
    <form method="post" action="/scan-log/delete-all" id="scanlogDeleteAllForm"></form>
    <script>
      (function() {{
        var allCheck = document.getElementById('scanlogAllCheck');
        var selectBtn = document.getElementById('scanlogSelectAllBtn');
        var deleteAllBtn = document.getElementById('scanlogDeleteAllBtn');
        function applyAll(checked) {{
          document.querySelectorAll('.scanlog-check').forEach(function(c) {{ c.checked = checked; }});
          if (allCheck) allCheck.checked = checked;
        }}
        if (allCheck) {{ allCheck.addEventListener('click', function() {{ applyAll(allCheck.checked); }}); }}
        if (selectBtn) {{
          selectBtn.addEventListener('click', function() {{
            var next = !(allCheck && allCheck.checked);
            applyAll(next);
          }});
        }}
        if (deleteAllBtn) {{
          deleteAllBtn.addEventListener('click', function() {{
            if (confirm('전체 스캔 이력을 삭제합니다. 되돌릴 수 없습니다.')) {{
              document.getElementById('scanlogDeleteAllForm').submit();
            }}
          }});
        }}
      }})();
    </script>
    """
    return HTMLResponse(content=render_page(content, user, "scanlog"))


@app.post("/scan-log/delete")
async def scan_log_delete(
    request: Request,
    session_token: str = Cookie(default=None)
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ids = form.getlist("log_ids")
    if ids:
        conn = get_conn()
        logs_to_delete = conn.execute(
            f"SELECT * FROM scan_log WHERE id IN ({','.join('?' for _ in ids)})",
            [int(i) for i in ids]
        ).fetchall()
        for lg in logs_to_delete:
            revert_delta = -1 if lg["scan_type"] == "IN" else 1
            adjust_quantity(lg["branch_code"], lg["item_code"], revert_delta)
        conn.execute(
            f"DELETE FROM scan_log WHERE id IN ({','.join('?' for _ in ids)})",
            [int(i) for i in ids]
        )
        conn.commit()
        conn.close()
    return RedirectResponse(url="/scan-log", status_code=303)


@app.post("/scan-log/delete-all")
async def scan_log_delete_all(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/scan-log", status_code=303)
    conn = get_conn()
    if user["role"] == "master":
        query = "SELECT * FROM scan_log"
        params = []
    logs_to_delete = conn.execute(query, params).fetchall()
    for lg in logs_to_delete:
        revert_delta = -1 if lg["scan_type"] == "IN" else 1
        adjust_quantity(lg["branch_code"], lg["item_code"], revert_delta)
    conn.execute("DELETE FROM scan_log")
    conn.commit()
    conn.close()
    return RedirectResponse(url="/scan-log", status_code=303)

# ── 헬스체크 ────────────────────────────────────────────

# ── PWA ──────────────────────────────────────────────

@app.get("/manifest.json")
async def pwa_manifest():
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "재고 관리 시스템",
        "short_name": "재고관리",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f5f7fa",
        "theme_color": "#1E2761",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.get("/sw.js")
async def pwa_service_worker():
    from fastapi.responses import Response
    js = """
const CACHE_NAME = 'inventory-sync-v1';
self.addEventListener('install', function(event) {
  self.skipWaiting();
});
self.addEventListener('activate', function(event) {
  self.clients.claim();
});
self.addEventListener('fetch', function(event) {
  // 네트워크 우선, 실패 시 아무 것도 안 함 (재고 데이터는 항상 최신이어야 하므로 캐시 저장 안 함)
  event.respondWith(
    fetch(event.request).catch(function() {
      return new Response('오프라인 상태입니다. 네트워크 연결을 확인해주세요.', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' }
      });
    })
  );
});
"""
    return Response(content=js, media_type="application/javascript")


@app.get("/icon-192.png")
async def pwa_icon_192():
    return _generate_app_icon(192)


@app.get("/icon-512.png")
async def pwa_icon_512():
    return _generate_app_icon(512)


def _generate_app_icon(size: int):
    from fastapi.responses import Response
    from PIL import Image, ImageDraw, ImageFont
    import io
    img = Image.new("RGB", (size, size), "#1E2761")
    draw = ImageDraw.Draw(img)
    text = "📦"
    try:
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic-Bold.ttf")
        font = ImageFont.truetype(font_path, int(size * 0.3))
        label = "재고"
        bbox = draw.textbbox((0, 0), label, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - w) / 2, (size - h) / 2 - bbox[1]), label, fill="white", font=font)
    except Exception:
        pass
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# ── 마스터 전용 페이지 ──────────────────────────────────

@app.get("/master", response_class=HTMLResponse)
async def master_page(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    raw_count = conn.execute("SELECT COUNT(*) AS cnt FROM raw_inventory").fetchone()["cnt"]
    item_count = conn.execute("SELECT COUNT(*) AS cnt FROM items").fetchone()["cnt"]
    conn.close()

    content = f"""
    <h2 style="margin-bottom:16px;">⚙️ 마스터 관리</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <a href="/master/data" style="text-decoration:none;">
        <div class="card" style="text-align:center;padding:24px;cursor:pointer;">
          <div style="font-size:32px;">📋</div>
          <div style="font-weight:bold;color:#1E2761;margin-top:8px;">데이터 관리</div>
          <div style="color:#888;font-size:12px;margin-top:4px;">품목 {item_count}개 등록됨</div>
        </div>
      </a>
      <a href="/master/qr-init" style="text-decoration:none;">
        <div class="card" style="text-align:center;padding:24px;cursor:pointer;">
          <div style="font-size:32px;">🔄</div>
          <div style="font-weight:bold;color:#1E2761;margin-top:8px;">QR 재고 업로드</div>
          <div style="color:#888;font-size:12px;margin-top:4px;">엑셀로 초기 수량 업로드</div>
        </div>
      </a>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "master"))


# ── 마스터 > 데이터 관리 ────────────────────────────────

@app.get("/master/data", response_class=HTMLResponse)
async def master_data_page(
    session_token: str = Cookie(default=None),
    filter_branch: str = "",
    search: str = ""
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    query = "SELECT * FROM items WHERE 1=1"
    params: list = []
    if filter_branch:
        query += " AND branch_code=?"
        params.append(filter_branch)
    if search:
        query += " AND (item_name LIKE ? OR item_code LIKE ? OR branch_name LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    query += " ORDER BY branch_code, item_name"
    items = conn.execute(query, params).fetchall()
    all_branches = conn.execute(
        "SELECT DISTINCT branch_code, branch_name FROM items ORDER BY branch_code"
    ).fetchall()
    conn.close()

    branch_options = '<option value="">전체 지점</option>'
    for b in all_branches:
        sel = "selected" if filter_branch == b["branch_code"] else ""
        branch_options += f'<option value="{b["branch_code"]}" {sel}>{b["branch_name"]}</option>'

    rows_html = ""
    if not items:
        rows_html = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#888;">데이터 없음</td></tr>'
    else:
        for it in items:
            rows_html += f"""
            <tr>
              <td style="text-align:center;">
                <input type="checkbox" name="selected_ids" value="{it['id']}"
                       class="master-data-check" style="width:16px;height:16px;">
              </td>
              <td>{it['branch_name']}</td>
              <td>{it['item_name']}</td>
              <td>{it['item_code']}</td>
              <td>{it['created_at'][:10] if it['created_at'] else '-'}</td>
            </tr>"""

    content = f"""
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;">
      <a href="/master" style="color:#1E2761;">← 마스터</a>
      <h2>📋 데이터 관리</h2>
    </div>

    <div class="card">
      <h3 style="margin-bottom:12px;">➕ 수기 등록</h3>
      <form method="post" action="/master/data/add">
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <div style="flex:1;min-width:130px;">
            <label style="font-size:12px;color:#888;">지점</label>
            <select name="branch_code" required style="margin-top:4px;">
              <option value="">선택</option>
              {''.join(f'<option value="{b["branch_code"]}">{b["branch_name"]}</option>' for b in BRANCHES)}
            </select>
          </div>
          <div style="flex:1;min-width:130px;">
            <label style="font-size:12px;color:#888;">상품명</label>
            <input name="item_name" required placeholder="상품명" style="margin-top:4px;">
          </div>
          <div style="flex:1;min-width:130px;">
            <label style="font-size:12px;color:#888;">품번</label>
            <input name="item_code" required placeholder="품번" style="margin-top:4px;">
          </div>
          <div style="flex:1;min-width:100px;">
            <label style="font-size:12px;color:#888;">초기 수량</label>
            <input name="init_quantity" type="number" value="0" style="margin-top:4px;">
          </div>
        </div>
        <button class="btn" type="submit" style="margin-top:12px;">등록</button>
      </form>
    </div>

    <div class="card">
      <h3 style="margin-bottom:12px;">📂 엑셀 업로드</h3>
      <p style="color:#666;font-size:12px;margin-bottom:8px;">컬럼: A=지점명 | B=상품명 | C=품번 (1행 헤더)</p>
      <form method="post" action="/master/data/upload" enctype="multipart/form-data"
            style="display:flex;gap:8px;align-items:center;">
        <input type="file" name="file" accept=".xlsx,.xls" style="width:auto;flex:1;">
        <button class="btn" type="submit">업로드</button>
      </form>
    </div>

    <div class="card">
      <form method="get" action="/master/data"
            style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;">
        <div style="flex:1;min-width:120px;">
          <label style="font-size:12px;color:#888;">지점 필터</label>
          <select name="filter_branch" style="margin-top:4px;">{branch_options}</select>
        </div>
        <div style="flex:2;min-width:160px;">
          <label style="font-size:12px;color:#888;">검색</label>
          <input name="search" value="{search}" placeholder="상품명/품번 검색"
                 style="margin-top:4px;">
        </div>
        <button class="btn" type="submit">검색</button>
        <a href="/master/data" style="padding:10px 14px;background:#eee;
           border-radius:8px;font-size:13px;text-decoration:none;color:#555;">초기화</a>
      </form>
    </div>

    <div class="card">
      <form method="post" action="/master/data/delete-selected" id="listForm">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <h3>품목 목록 ({len(items)}개)</h3>
          <div style="display:flex;gap:8px;">
            <button type="button" class="btn" id="masterDataSelectAllBtn"
                    style="background:#64748B;font-size:12px;padding:6px 12px;">전체선택</button>
            <button type="submit" class="btn btn-red"
                    style="font-size:12px;padding:6px 12px;"
                    onclick="return confirm('선택 항목을 삭제할까요?')">선택삭제</button>
            <button type="button" class="btn btn-red" id="masterDataDeleteAllBtn"
                    style="font-size:12px;padding:6px 12px;">전체삭제</button>
          </div>
        </div>
        <table>
          <thead><tr>
            <th style="width:40px;text-align:center;">
              <input type="checkbox" id="masterDataAllCheck" style="width:16px;height:16px;">
            </th>
            <th>지점명</th><th>상품명</th><th>품번</th><th>등록일</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </form>
    </div>
    <form method="post" action="/master/data/delete-all" id="masterDataDeleteAllForm"></form>
    <script>
      (function() {{
        var allCheck = document.getElementById('masterDataAllCheck');
        var selectBtn = document.getElementById('masterDataSelectAllBtn');
        var deleteAllBtn = document.getElementById('masterDataDeleteAllBtn');
        function applyAll(checked) {{
          document.querySelectorAll('.master-data-check').forEach(function(c) {{ c.checked = checked; }});
          if (allCheck) allCheck.checked = checked;
        }}
        if (allCheck) {{
          allCheck.addEventListener('click', function() {{ applyAll(allCheck.checked); }});
        }}
        if (selectBtn) {{
          selectBtn.addEventListener('click', function() {{
            var next = !(allCheck && allCheck.checked);
            applyAll(next);
          }});
        }}
        if (deleteAllBtn) {{
          deleteAllBtn.addEventListener('click', function() {{
            if (confirm('전체 품목을 삭제합니다. 되돌릴 수 없습니다.')) {{
              document.getElementById('masterDataDeleteAllForm').submit();
            }}
          }});
        }}
      }})();
    </script>
    """
    return HTMLResponse(content=render_page(content, user, "master"))


@app.post("/master/data/add")
async def master_data_add(
    session_token: str = Cookie(default=None),
    branch_code: str = Form(...),
    item_name: str = Form(...),
    item_code: str = Form(...),
    init_quantity: int = Form(0)
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)
    branch_name = next(
        (b["branch_name"] for b in BRANCHES if b["branch_code"] == branch_code), branch_code)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO items (branch_code, branch_name, item_name, item_code, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(branch_code, item_code) DO UPDATE SET item_name=excluded.item_name""",
            (branch_code, branch_name, item_name, item_code, datetime.now().isoformat())
        )
        conn.execute(
            """INSERT INTO inventory (branch_code, item_name, item_code, quantity, last_updated)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(branch_code, item_code) DO UPDATE SET
                 quantity=excluded.quantity, item_name=excluded.item_name, last_updated=excluded.last_updated""",
            (branch_code, item_name, item_code, init_quantity, datetime.now().isoformat())
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return RedirectResponse(url="/master/data", status_code=303)


@app.post("/master/data/upload")
async def master_data_upload(
    session_token: str = Cookie(default=None),
    file: UploadFile = File(...)
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)
    contents = await file.read()
    import io
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb.active
    if ws is None:
        return RedirectResponse(url="/master/data", status_code=303)
    branch_map = {}
    for b in BRANCHES:
        branch_map[b["branch_name"]] = b["branch_code"]
        branch_map[b["branch_name"].replace(" ", "")] = b["branch_code"]
        branch_map[b["branch_code"]] = b["branch_code"]
    conn = get_conn()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        branch_name = str(row[0]).strip()
        item_name = str(row[1]).strip() if row[1] else ""
        item_code = str(row[2]).strip() if row[2] else ""
        branch_code = (branch_map.get(branch_name)
                       or branch_map.get(branch_name.replace(" ", ""))
                       or branch_name)
        try:
            conn.execute(
                """INSERT INTO items (branch_code, branch_name, item_name, item_code, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(branch_code, item_code) DO UPDATE SET item_name=excluded.item_name""",
                (branch_code, branch_name, item_name, item_code, datetime.now().isoformat())
            )
        except Exception:
            continue
    conn.commit()
    conn.close()
    return RedirectResponse(url="/master/data", status_code=303)


@app.post("/master/data/delete-selected")
async def master_data_delete_selected(
    request: Request,
    session_token: str = Cookie(default=None)
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ids = form.getlist("selected_ids")
    if ids:
        conn = get_conn()
        conn.execute(
            f"DELETE FROM items WHERE id IN ({','.join('?' for _ in ids)})",
            [int(i) for i in ids]
        )
        conn.commit()
        conn.close()
    return RedirectResponse(url="/master/data", status_code=303)


@app.post("/master/data/delete-all")
async def master_data_delete_all(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)
    conn = get_conn()
    conn.execute("DELETE FROM items")
    conn.commit()
    conn.close()
    return RedirectResponse(url="/master/data", status_code=303)


# ── 유비플러스 재고 (RAW 업로드) ────────────────────────

@app.get("/master/raw-upload", response_class=HTMLResponse)
async def raw_upload_page(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_conn()
    raws = conn.execute(
        "SELECT * FROM raw_inventory ORDER BY branch_code, item_code"
    ).fetchall()
    conn.close()

    rows_html = ""
    if not raws:
        rows_html = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#888;">업로드된 데이터 없음</td></tr>'
    else:
        for r in raws:
            rows_html += f"""
            <tr>
              <td>{r['branch_name']}</td>
              <td>{r['item_name']}</td>
              <td>{r['item_code']}</td>
              <td style="font-weight:bold;">{r['quantity']}</td>
              <td>{r['uploaded_at'][:10] if r['uploaded_at'] else '-'}</td>
            </tr>"""

    content = f"""
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;">
      <a href="/master" style="color:#1E2761;">← 마스터</a>
      <h2>📤 유비플러스 재고</h2>
    </div>
    <div class="card" style="background:#FFF7ED;border:1px solid #FCD34D;">
      <p style="font-size:13px;color:#92400E;">
        ⚠️ MSSQL 연동 전 임시 기능입니다. 업로드한 데이터가 대시보드 비교 기준(RAW)으로 사용됩니다.
        H열/Q열 값은 QR재고에 자동 가산됩니다.
      </p>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;">엑셀 업로드</h3>
      <p style="color:#666;font-size:12px;margin-bottom:12px;">
        컬럼 위치: <b>A=지점명 / B=상품명 / D=품번 / N=현재수량 / H,Q=QR재고 가산분</b> (1행 헤더)
      </p>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="file" id="rawFile" accept=".xlsx,.xls" style="width:auto;flex:1;">
        <button class="btn" type="button" onclick="uploadRaw()">업로드</button>
      </div>
      <div id="uploadResult" style="display:none;margin-top:12px;padding:12px;
           border-radius:8px;font-size:13px;"></div>
      <script>
      async function uploadRaw() {{
        const file = document.getElementById('rawFile').files[0];
        if (!file) {{ alert('파일을 선택해주세요.'); return; }}
        const fd = new FormData();
        fd.append('file', file);
        const btn = event.target;
        btn.textContent = '업로드 중...';
        btn.disabled = true;
        try {{
          const res = await fetch('/master/raw-upload/ajax', {{
            method: 'POST', body: fd
          }});
          const data = await res.json();
          const box = document.getElementById('uploadResult');
          box.style.display = 'block';
          box.style.background = data.errors.length ? '#FEF9C3' : '#D1FAE5';
          box.innerHTML = `<b>${{data.errors.length ? '⚠️' : '✅'}} 업로드 완료</b><br>
          헤더 인식 행: <b>${{data.header_row_used ?? '?'}}행</b><br>
          컬럼 매핑: <b>${{JSON.stringify(data.col_map_debug)}}</b><br>
          성공: <b style="color:#22C55E">${{data.success}}건</b> &nbsp;
          실패: <b style="color:#EF4444">${{data.skipped}}건</b>
          ${{data.errors.length ? '<ul>' + data.errors.map(e=>`<li style="color:#EF4444;font-size:12px;">${{e}}</li>`).join('') + '</ul>' : ''}}
          ${{data.hq_debug && data.hq_debug.length ? '<br><b>H/Q 반영 내역:</b><ul>' + data.hq_debug.map(e=>`<li style="font-size:12px;">${{e}}</li>`).join('') + '</ul>' : '<br><span style="color:#F59E0B;">⚠️ H/Q 반영된 품목 없음</span>'}}`;
          setTimeout(() => location.reload(), 2000);
        }} catch(e) {{
          alert('업로드 중 오류가 발생했습니다.');
        }} finally {{
          btn.textContent = '업로드';
          btn.disabled = false;
        }}
      }}
      </script>
    </div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <h3>현재 유비플러스 데이터 ({len(raws)}개)</h3>
        <form method="post" action="/master/raw-upload/clear">
          <button type="submit" class="btn btn-red"
                  style="font-size:12px;padding:6px 12px;"
                  onclick="return confirm('전체 데이터를 삭제합니다.')">전체 초기화</button>
        </form>
      </div>
      <table>
        <thead><tr>
          <th>지점명</th><th>상품명</th><th>품번</th><th>수량</th><th>업로드일</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "master"))


@app.post("/master/raw-upload/ajax")
async def raw_upload_ajax(
    session_token: str = Cookie(default=None),
    file: UploadFile = File(...)
):
    user = get_session(session_token)
    if not user:
        return {"success": 0, "skipped": 0, "errors": ["로그인이 필요합니다"]}
    return await _process_raw_upload_master(file)

async def _process_raw_upload_master(file: UploadFile):
    """마스터 전용 — 헤더가 2행에 있고 컬럼명이 다른 '재고수불부' 형식 처리
    ⚠️ 2026-07 수정: 업로드된 엑셀에 실제로 존재하는 지점만 삭제/갱신하도록 변경
       (기존 버그: source='master' 전체를 지워서 다른 지점 데이터가 0으로 변함)
    """
    contents = await file.read()
    import io
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb.active
    if ws is None:
        return {"success": 0, "skipped": 0, "errors": ["시트를 찾을 수 없습니다."]}

    header_row_idx = None
    col_map = {}
    KEYWORDS = {
        "branch": ["지점"],
        "item_name": ["상품명"],
        "item_code": ["품번"],
        "qty": ["기말수량"],
        "h": ["증가수량"],
        "q": ["재고조정"],
    }
    for row_idx in range(1, 6):
        row_vals = next(ws.iter_rows(min_row=row_idx, max_row=row_idx, values_only=True), None)
        if not row_vals:
            continue
        found = {}
        for col_idx, cell_val in enumerate(row_vals):
            if not cell_val:
                continue
            text = str(cell_val).strip()
            for key, keywords in KEYWORDS.items():
                if key in found:
                    continue
                if any(kw in text for kw in keywords):
                    found[key] = col_idx
        if all(k in found for k in ("branch", "item_name", "item_code")):
            header_row_idx = row_idx
            col_map = found
            break

    if header_row_idx is None:
        return {"success": 0, "skipped": 0,
                "errors": ["헤더를 찾을 수 없습니다. '지점/상품명/품번' 컬럼명이 포함된 행이 있는지 확인해주세요."]}

    branch_map = {}
    for b in BRANCHES:
        branch_map[b["branch_name"]] = b["branch_code"]
        branch_map[b["branch_name"].replace(" ", "")] = b["branch_code"]
        branch_map[b["branch_code"]] = b["branch_code"]

    now = datetime.now().isoformat()
    success, skipped, errors = 0, 0, []
    hq_adjustments = []
    debug_hq_log = []
    data_start_row = header_row_idx + 1

    # ── ⚠️ 1단계: 삭제 전에 먼저 "이번 엑셀에 실제로 어떤 지점이 있는지" 수집 ──
    branches_in_file = set()
    parsed_rows = []
    for idx, row in enumerate(ws.iter_rows(min_row=data_start_row, values_only=True), start=data_start_row):
        branch_col = col_map.get("branch")
        if branch_col is None or branch_col >= len(row) or not row[branch_col]:
            continue
        branch_name = str(row[branch_col]).strip()
        branch_code = (branch_map.get(branch_name)
                       or branch_map.get(branch_name.replace(" ", ""))
                       or branch_name)
        branches_in_file.add(branch_code)
        parsed_rows.append((idx, row, branch_code, branch_name))

    # ── ⚠️ 빈 데이터 방어: 엑셀에서 유효한 지점 행을 하나도 못 찾으면 삭제 자체를 하지 않고 즉시 중단 ──
    if not branches_in_file:
        return {"success": 0, "skipped": 0,
                "errors": ["엑셀에서 유효한 지점 데이터를 찾지 못했습니다. 기존 데이터는 보존되었으며 아무 것도 변경되지 않았습니다."],
                "header_row_used": header_row_idx,
                "col_map_debug": {k: v for k, v in col_map.items()}}

    conn = get_conn()
    # ── ⚠️ 핵심 수정: source='master' 전체가 아니라, 이번 엑셀에 있는 지점만 삭제 ──
    for bc in branches_in_file:
        conn.execute("DELETE FROM raw_inventory WHERE source='master' AND branch_code=?", (bc,))
    old_hq_rows = conn.execute("SELECT * FROM hq_bonus_log").fetchall()
    old_hq_map = {f"{r['branch_code']}|{r['item_code']}": r["last_hq_total"] for r in old_hq_rows}
    # ── ⚠️ hq_bonus_log도 전체 삭제 대신 해당 지점만 삭제 ──
    for bc in branches_in_file:
        conn.execute("DELETE FROM hq_bonus_log WHERE branch_code=?", (bc,))
    conn.commit()

    for idx, row, branch_code, branch_name in parsed_rows:
        try:
            item_name = str(row[col_map["item_name"]]).strip() if row[col_map["item_name"]] else ""
            item_code = str(row[col_map["item_code"]]).strip() if row[col_map["item_code"]] else ""

            if not item_code:
                item_code = f"미지정_{branch_name}_{item_name}"[:50]

            qty_n = row[col_map["qty"]] if "qty" in col_map and col_map["qty"] < len(row) else None
            qty_h = row[col_map["h"]] if "h" in col_map and col_map["h"] < len(row) else None
            qty_q = row[col_map["q"]] if "q" in col_map and col_map["q"] < len(row) else None

            raw_quantity = int(float(str(qty_n))) if qty_n not in (None, "") else 0
            add_h = int(float(str(qty_h))) if qty_h not in (None, "") else 0
            add_q = int(float(str(qty_q))) if qty_q not in (None, "") else 0
            hq_total = add_h + add_q

            conn.execute("""
                INSERT INTO raw_inventory
                  (branch_code, branch_name, item_name, item_code, quantity, source, uploaded_at)
                VALUES (?, ?, ?, ?, ?, 'master', ?)
                ON CONFLICT(branch_code, item_code, source) DO UPDATE SET
                  quantity=excluded.quantity,
                  item_name=excluded.item_name,
                  branch_name=excluded.branch_name,
                  uploaded_at=excluded.uploaded_at
            """, (branch_code, branch_name, item_name, item_code, raw_quantity, now))

            if hq_total != 0:
                hq_adjustments.append((branch_code, item_name, item_code, hq_total))
                debug_hq_log.append(f"{item_name}({item_code}): 증가={add_h}, 조정={add_q}, 합계={hq_total}")

            success += 1
        except Exception as e:
            errors.append(f"행 {idx}: {str(e)[:50]}")
            skipped += 1

    conn.commit()
    conn.close()

    for branch_code, item_name, item_code, new_hq_total in hq_adjustments:
        conn2 = get_conn()
        prev_hq_total = old_hq_map.get(f"{branch_code}|{item_code}", 0)
        net_delta = new_hq_total - prev_hq_total
        if net_delta != 0:
            new_qty = adjust_quantity(branch_code, item_code, net_delta, absolute=False)
            conn2.execute(
                "INSERT INTO adjustment_log (branch_code, item_name, item_code, delta, result_quantity, adjusted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (branch_code, item_name, item_code, net_delta, new_qty, now)
            )
        conn2.execute("""
            INSERT INTO hq_bonus_log (branch_code, item_code, last_hq_total, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(branch_code, item_code) DO UPDATE SET
              last_hq_total=excluded.last_hq_total, updated_at=excluded.updated_at
        """, (branch_code, item_code, new_hq_total, now))
        conn2.commit()
        conn2.close()

    return {"success": success, "skipped": skipped, "errors": errors[:10],
            "header_row_used": header_row_idx,
            "hq_debug": debug_hq_log[:20],
            "col_map_debug": {k: v for k, v in col_map.items()}}


async def _process_raw_upload(file: UploadFile, restrict_branch: Optional[str] = None):
    contents = await file.read()
    import io
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb.active
    if ws is None:
        return {"success": 0, "skipped": 0, "errors": ["시트를 찾을 수 없습니다."]}

    # ── 헤더 행 자동 탐색 (1행 또는 2행, 병합 셀 대응) ──
    # 최대 5행까지 훑어서 "품번" 또는 "현재수량" 텍스트가 있는 행을 헤더로 인식
    header_row_idx = None
    col_map = {}  # {"branch":0, "item_name":1, "item_code":3, "qty":13, "h":7, "q":16}

    KEYWORDS = {
        "branch": ["지점", "지점명"],
        "item_name": ["상품명", "품명"],
        "item_code": ["품번", "품목코드"],
        "qty": ["현재고", "기말수량"],
        "h": ["H", "증가수량"],
        "q": ["Q", "재고조정"],
    }

    for row_idx in range(1, 6):
        row_vals = next(ws.iter_rows(min_row=row_idx, max_row=row_idx, values_only=True), None)
        if not row_vals:
            continue
        found = {}
        for col_idx, cell_val in enumerate(row_vals):
            if not cell_val:
                continue
            text = str(cell_val).strip()
            for key, keywords in KEYWORDS.items():
                if key in found:
                    continue
                if any(kw in text for kw in keywords):
                    found[key] = col_idx
        # 최소한 지점/상품명/품번 세 개는 찾아야 이 행을 헤더로 인정
        if all(k in found for k in ("branch", "item_name", "item_code")):
            header_row_idx = row_idx
            col_map = found
            break

    if header_row_idx is None:
        return {"success": 0, "skipped": 0,
                "errors": ["헤더를 찾을 수 없습니다. '지점명/상품명/품번' 컬럼명이 포함된 행이 있는지 확인해주세요."]}

    branch_map = {}
    for b in BRANCHES:
        branch_map[b["branch_name"]] = b["branch_code"]
        branch_map[b["branch_name"].replace(" ", "")] = b["branch_code"]
        branch_map[b["branch_code"]] = b["branch_code"]

    now = datetime.now().isoformat()
    success, skipped, errors = 0, 0, []
    hq_adjustments = []
    debug_hq_log = []


    conn = get_conn()
    data_start_row = header_row_idx + 1
    # ── 이전 업로드 데이터 삭제 (지점 제한 있으면 해당 지점만, 없으면 전체 - 마스터) ──
    if restrict_branch:
        conn.execute("DELETE FROM raw_inventory WHERE branch_code=? AND source='branch'", (restrict_branch,))
    else:
        conn.execute("DELETE FROM raw_inventory WHERE source='branch'")
    # 이전 H/Q 반영 이력도 초기화 (재계산 기준점 리셋)
    old_hq_rows = conn.execute("SELECT * FROM hq_bonus_log").fetchall()
    old_hq_map = {f"{r['branch_code']}|{r['item_code']}": r["last_hq_total"] for r in old_hq_rows}
    conn.execute("DELETE FROM hq_bonus_log")
    conn.commit()
    

    for idx, row in enumerate(ws.iter_rows(min_row=data_start_row, values_only=True), start=data_start_row):
        branch_col = col_map.get("branch")
        if branch_col is None or branch_col >= len(row) or not row[branch_col]:
            continue
        try:
            branch_name = str(row[col_map["branch"]]).strip()
            item_name = str(row[col_map["item_name"]]).strip() if row[col_map["item_name"]] else ""
            item_code = str(row[col_map["item_code"]]).strip() if row[col_map["item_code"]] else ""

            if not item_code:
                item_code = f"미지정_{branch_name}_{item_name}"[:50]

            qty_n = row[col_map["qty"]] if "qty" in col_map and col_map["qty"] < len(row) else None
            qty_h = row[col_map["h"]] if "h" in col_map and col_map["h"] < len(row) else None
            qty_q = row[col_map["q"]] if "q" in col_map and col_map["q"] < len(row) else None

            raw_quantity = int(float(str(qty_n))) if qty_n not in (None, "") else 0
            add_h = int(float(str(qty_h))) if qty_h not in (None, "") else 0
            add_q = int(float(str(qty_q))) if qty_q not in (None, "") else 0
            hq_total = add_h + add_q

            branch_code = (branch_map.get(branch_name)
                           or branch_map.get(branch_name.replace(" ", ""))
                           or branch_name)
            
            if restrict_branch and branch_code != restrict_branch:
                skipped += 1
                continue

            conn.execute("""
                INSERT INTO raw_inventory
                  (branch_code, branch_name, item_name, item_code, quantity, source, uploaded_at)
                VALUES (?, ?, ?, ?, ?, 'branch', ?)
                ON CONFLICT(branch_code, item_code, source) DO UPDATE SET
                  quantity=excluded.quantity,
                  item_name=excluded.item_name,
                  branch_name=excluded.branch_name,
                  uploaded_at=excluded.uploaded_at
            """, (branch_code, branch_name, item_name, item_code, raw_quantity, now))

            if hq_total != 0:
                hq_adjustments.append((branch_code, item_name, item_code, hq_total))
                debug_hq_log.append(f"{item_name}({item_code}): H={add_h}, Q={add_q}, 합계={hq_total}")

            success += 1
            
        except Exception as e:
            errors.append(f"행 {idx}: {str(e)[:50]}")
            skipped += 1

    conn.commit()
    conn.close()

    for branch_code, item_name, item_code, new_hq_total in hq_adjustments:
        conn2 = get_conn()
        prev_hq_total = old_hq_map.get(f"{branch_code}|{item_code}", 0)

        # 이전에 반영했던 만큼 빼고, 새 값을 더함 → 결과적으로 "덮어쓰기" 효과
        net_delta = new_hq_total - prev_hq_total

        if net_delta != 0:
            new_qty = adjust_quantity(branch_code, item_code, net_delta, absolute=False)
            conn2.execute(
                "INSERT INTO adjustment_log (branch_code, item_name, item_code, delta, result_quantity, adjusted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (branch_code, item_name, item_code, net_delta, new_qty, now)
            )

        conn2.execute("""
            INSERT INTO hq_bonus_log (branch_code, item_code, last_hq_total, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(branch_code, item_code) DO UPDATE SET
              last_hq_total=excluded.last_hq_total,
              updated_at=excluded.updated_at
        """, (branch_code, item_code, new_hq_total, now))

        conn2.commit()
        conn2.close()

    return {"success": success, "skipped": skipped, "errors": errors[:10],
            "header_row_used": header_row_idx,
            "hq_debug": debug_hq_log[:20],
            "col_map_debug": {k: v for k, v in col_map.items()}}

@app.post("/master/raw-upload/clear")
async def raw_upload_clear(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    conn = get_conn()
    conn.execute("DELETE FROM raw_inventory")
    conn.commit()
    conn.close()
    return RedirectResponse(url="/master/raw-upload", status_code=303)


@app.get("/raw-upload", response_class=HTMLResponse)
async def raw_upload_redirect(session_token: str = Cookie(default=None)):
    """구 경로 호환용 - 역할별 분기"""
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user["role"] == "master":
        return RedirectResponse(url="/master/raw-upload", status_code=303)
    return RedirectResponse(url="/raw-branch", status_code=303)

@app.get("/raw-branch", response_class=HTMLResponse)
async def raw_branch_page(session_token: str = Cookie(default=None)):
    """유비플러스 재고 — 지점 계정: 본인 지점만 업로드/조회, 마스터: 전체 접근 가능 (마스터 전용 화면과 별개로 지점 시점 확인용)"""
    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    target_branch = user["branch_code"] if user["role"] == "branch" else ""

    conn = get_conn()
    if target_branch:
        raws = conn.execute(
            "SELECT * FROM raw_inventory WHERE branch_code=? ORDER BY item_code",
            (target_branch,)
        ).fetchall()
    else:
        raws = conn.execute("SELECT * FROM raw_inventory ORDER BY branch_code, item_code").fetchall()
    conn.close()

    rows_html = ""
    if not raws:
        rows_html = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#888;">데이터 없음</td></tr>'
    else:
        for r in raws:
            rows_html += f"""
            <tr>
              <td>{r['branch_name']}</td>
              <td>{r['item_name']}</td>
              <td>{r['item_code']}</td>
              <td style="font-weight:bold;">{r['quantity']}</td>
              <td>{r['uploaded_at'][:10] if r['uploaded_at'] else '-'}</td>
            </tr>"""

    upload_note = "본인 지점 데이터만 업로드/조회됩니다." if target_branch else "마스터 계정 — 전체 지점 데이터가 조회됩니다. 업로드 시 엑셀 내 지점 컬럼 기준으로 반영됩니다."

    content = f"""
    <h2 style="margin-bottom:16px;">📤 유비플러스 재고</h2>
    <div class="card" style="background:#FFF7ED;border:1px solid #FCD34D;">
      <p style="font-size:13px;color:#92400E;">⚠️ {upload_note} H열/Q열 값은 QR재고에 자동 가산됩니다.</p>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;">엑셀 업로드</h3>
      <p style="color:#666;font-size:12px;margin-bottom:12px;">
        컬럼 위치: <b>A=지점명 / B=상품명 / D=품번 / N=현재수량 / H,Q=QR재고 가산분</b> (1행 헤더)
      </p>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="file" id="rawFile" accept=".xlsx,.xls" style="width:auto;flex:1;">
        <button class="btn" type="button" onclick="uploadRawBranch()">업로드</button>
      </div>
      <div id="uploadResult" style="display:none;margin-top:12px;padding:12px;
           border-radius:8px;font-size:13px;"></div>
      <script>
      async function uploadRawBranch() {{
        const file = document.getElementById('rawFile').files[0];
        if (!file) {{ alert('파일을 선택해주세요.'); return; }}
        const fd = new FormData();
        fd.append('file', file);
        const btn = event.target;
        btn.textContent = '업로드 중...';
        btn.disabled = true;
        try {{
          const res = await fetch('/raw-branch/upload', {{ method: 'POST', body: fd }});
          const data = await res.json();
          const box = document.getElementById('uploadResult');
          box.style.display = 'block';
          box.style.background = data.errors && data.errors.length ? '#FEF9C3' : '#D1FAE5';
          box.innerHTML = `<b>${{data.errors && data.errors.length ? '⚠️' : '✅'}} 업로드 완료</b><br>
          성공: <b style="color:#22C55E">${{data.success}}건</b> &nbsp;
          실패: <b style="color:#EF4444">${{data.skipped}}건</b>
          ${{data.errors && data.errors.length ? '<ul>' + data.errors.map(e=>`<li style="color:#EF4444;font-size:12px;">${{e}}</li>`).join('') + '</ul>' : ''}}`;
          setTimeout(() => location.reload(), 2000);
        }} catch(e) {{
          alert('업로드 중 오류가 발생했습니다.');
        }} finally {{
          btn.textContent = '업로드';
          btn.disabled = false;
        }}
      }}
      </script>
    </div>
    <div class="card">
      <h3 style="margin-bottom:12px;">현재 데이터 ({len(raws)}개)</h3>
      <table>
        <thead><tr>
          <th>지점명</th><th>상품명</th><th>품번</th><th>수량</th><th>업로드일</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "raw-branch"))


@app.post("/raw-branch/upload")
async def raw_branch_upload(
    session_token: str = Cookie(default=None),
    file: UploadFile = File(...)
):
    """지점 계정 업로드 — 본인 지점 데이터만 반영 (엑셀에 다른 지점 있어도 무시)"""
    user = get_session(session_token)
    if not user:
        return {"success": 0, "skipped": 0, "errors": ["로그인이 필요합니다"]}

    result = await _process_raw_upload(file, restrict_branch=(user["branch_code"] if user["role"] == "branch" else None))
    return result


# ── 마스터 > QR 재고 업로드(초기화) ────────────────────

@app.get("/master/qr-init", response_class=HTMLResponse)
async def qr_init_page(session_token: str = Cookie(default=None)):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)

    content = """
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;">
      <a href="/master" style="color:#1E2761;">← 마스터</a>
      <h2>🔄 QR 재고 업로드</h2>
    </div>
    <div class="card" style="background:#FFF7ED;border:1px solid #FCD34D;">
      <p style="font-size:13px;color:#92400E;">
        ⚠️ 엑셀로 초기 수량을 업로드하면 기존 QR 재고 수량이 덮어쓰기됩니다.
      </p>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;">엑셀 업로드</h3>
      <p style="color:#666;font-size:12px;margin-bottom:12px;">
        컬럼: <b>A=지점명 / B=상품명 / C=품번 / D=초기수량</b> (1행 헤더)
      </p>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="file" id="qrInitFile" accept=".xlsx,.xls" style="width:auto;flex:1;">
        <button class="btn" type="button" onclick="uploadQrInit()" id="qrInitBtn">업로드</button>
      </div>
      <div id="qrInitResult" style="display:none;margin-top:12px;padding:12px;
           border-radius:8px;font-size:13px;"></div>
      <script>
      async function uploadQrInit() {
        const file = document.getElementById('qrInitFile').files[0];
        if (!file) { alert('파일을 선택해주세요.'); return; }
        const fd = new FormData();
        fd.append('file', file);
        const btn = document.getElementById('qrInitBtn');
        let seconds = 0;
        btn.disabled = true;
        const timerInterval = setInterval(() => {
          seconds++;
          btn.textContent = `업로드 중... (${seconds}초째)`;
        }, 1000);
        btn.textContent = '업로드 중... (0초째)';
        try {
          const res = await fetch('/master/qr-init/upload-ajax', { method: 'POST', body: fd });
          const data = await res.json();
          const box = document.getElementById('qrInitResult');
          box.style.display = 'block';
          box.style.background = data.errors && data.errors.length ? '#FEF9C3' : '#D1FAE5';
          box.innerHTML = `<b>${data.errors && data.errors.length ? '⚠️' : '✅'} 업로드 완료</b><br>
          성공: <b style="color:#22C55E">${data.success}건</b> &nbsp;
          실패: <b style="color:#EF4444">${data.skipped}건</b>
          ${data.errors && data.errors.length ? '<ul>' + data.errors.map(e=>`<li style="color:#EF4444;font-size:12px;">${e}</li>`).join('') + '</ul>' : ''}`;
        } catch(e) {
          alert('업로드 중 오류가 발생했습니다.');
        } finally {
          clearInterval(timerInterval);
          btn.textContent = '업로드';
          btn.disabled = false;
        }
      }
      </script>
    </div>
    """
    return HTMLResponse(content=render_page(content, user, "master"))


@app.post("/master/qr-init/upload-ajax")
async def qr_init_upload_ajax(
    session_token: str = Cookie(default=None),
    file: UploadFile = File(...)
):
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return {"success": 0, "skipped": 0, "errors": ["로그인이 필요합니다"]}

    contents = await file.read()
    import io
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb.active
    if ws is None:
        return {"success": 0, "skipped": 0, "errors": ["시트를 찾을 수 없습니다."]}

    branch_map = {}
    for b in BRANCHES:
        branch_map[b["branch_name"]] = b["branch_code"]
        branch_map[b["branch_name"].replace(" ", "")] = b["branch_code"]
        branch_map[b["branch_code"]] = b["branch_code"]

    conn = get_conn()
    now = datetime.now().isoformat()
    success, skipped, errors = 0, 0, []

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row[0]:
            continue
        try:
            branch_name = str(row[0]).strip()
            item_name = str(row[1]).strip() if row[1] else ""
            item_code = str(row[2]).strip() if row[2] else ""
            if not item_code:
                item_code = f"미지정_{branch_name}_{item_name}"[:50]
            init_qty = int(float(str(row[3]))) if row[3] is not None else 0
            branch_code = (branch_map.get(branch_name)
                           or branch_map.get(branch_name.replace(" ", ""))
                           or branch_name)

            conn.execute(
                """INSERT INTO inventory
                   (branch_code, item_name, item_code, quantity, last_updated)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(branch_code, item_code) DO UPDATE SET
                     quantity=excluded.quantity,
                     item_name=excluded.item_name,
                     last_updated=excluded.last_updated""",
                (branch_code, item_name, item_code, init_qty, now)
            )
            conn.execute(
                "INSERT INTO qr_init_log (branch_code, item_code, init_quantity, initialized_at) VALUES (?, ?, ?, ?)",
                (branch_code, item_code, init_qty, now)
            )
            success += 1
        except Exception as e:
            errors.append(f"행 {row_idx}: {str(e)[:50]}")
            skipped += 1
            continue

    conn.commit()
    conn.close()
    return {"success": success, "skipped": skipped, "errors": errors[:10]}


@app.post("/master/qr-init/upload")
async def qr_init_upload(
    session_token: str = Cookie(default=None),
    file: UploadFile = File(...)
):
    """구버전 호환용 - 폼 제출 방식 (리다이렉트만)"""
    user = get_session(session_token)
    if not user or user["role"] != "master":
        return RedirectResponse(url="/login", status_code=303)

    contents = await file.read()
    import io
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb.active
    if ws is None:
        return RedirectResponse(url="/master/qr-init", status_code=303)

    branch_map = {}
    for b in BRANCHES:
        branch_map[b["branch_name"]] = b["branch_code"]
        branch_map[b["branch_name"].replace(" ", "")] = b["branch_code"]
        branch_map[b["branch_code"]] = b["branch_code"]

    conn = get_conn()
    now = datetime.now().isoformat()

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        try:
            branch_name = str(row[0]).strip()
            item_name = str(row[1]).strip() if row[1] else ""
            item_code = str(row[2]).strip() if row[2] else ""
            if not item_code:
                item_code = f"미지정_{branch_name}_{item_name}"[:50]
            init_qty = int(float(str(row[3]))) if row[3] is not None else 0
            branch_code = (branch_map.get(branch_name)
                           or branch_map.get(branch_name.replace(" ", ""))
                           or branch_name)

            conn.execute(
                """INSERT INTO inventory
                   (branch_code, item_name, item_code, quantity, last_updated)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(branch_code, item_code) DO UPDATE SET
                     quantity=excluded.quantity,
                     item_name=excluded.item_name,
                     last_updated=excluded.last_updated""",
                (branch_code, item_name, item_code, init_qty, now)
            )
            conn.execute(
                "INSERT INTO qr_init_log (branch_code, item_code, init_quantity, initialized_at) VALUES (?, ?, ?, ?)",
                (branch_code, item_code, init_qty, now)
            )
        except Exception:
            continue

    conn.commit()
    conn.close()
    return RedirectResponse(url="/master/qr-init", status_code=303)


# ── 마스터 > QR 일괄 생성 (ZIP) ────────────────────────

@app.post("/master/qr/generate-bulk")
async def master_qr_generate_bulk(
    session_token: str = Cookie(default=None),
    branch_code: str = Form(...)
):
    import zipfile
    import io
    from urllib.parse import quote

    user = get_session(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if user["role"] != "master":
        branch_code = user["branch_code"]  # 지점 계정은 본인 지점 QR만 생성 가능

    conn = get_conn()
    if branch_code == "ALL":
        items = conn.execute("SELECT * FROM items ORDER BY branch_code, item_name").fetchall()
        zip_name = "전체지점_QR"
    else:
        items = conn.execute(
            "SELECT * FROM items WHERE branch_code=?", (branch_code,)
        ).fetchall()
        zip_name = f"{branch_code}_QR"
    conn.close()

    if not items:
        return HTMLResponse(content=render_page(
            '<div class="card"><p>❌ 등록된 품목이 없습니다.</p>'
            '<a href="/qr">← 돌아가기</a></div>', user, "qr"))

    hostname_env = os.getenv("PUBLIC_SERVER_URL")
    if hostname_env:
        server_url = hostname_env
    else:
        hostname = socket.gethostbyname(socket.gethostname())
        server_url = f"http://{hostname}:{SERVER_PORT}"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            for scan_type in ["IN", "OUT"]:
                img_bytes = generate_qr_bytes(
                    server_url, it["branch_code"],
                    it["item_code"], scan_type, it["item_name"]
                )
                filename = f"{it['branch_code']}_{it['item_code']}_{scan_type}.png"
                zf.writestr(f"{it['branch_code']}/{filename}", img_bytes)

    zip_buffer.seek(0)
    encoded_name = quote(f"{zip_name}.zip")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)