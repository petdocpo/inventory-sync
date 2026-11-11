# test_stock_closing.py — 재고 마감 기능 자동 테스트 (운영 DB/외부 발송 없이 모킹)
# 실행: python -m unittest test_stock_closing -v
# 표준 라이브러리 unittest만 사용. main.py는 읽기 전용, 모든 DB/발송은 FakeConn/모킹으로 대체.
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import main
from fastapi import HTTPException
from fastapi.responses import JSONResponse

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
YESTERDAY = TODAY - timedelta(days=1)
DAY_BEFORE = TODAY - timedelta(days=2)


def dstr(d):
    return d.strftime("%Y-%m-%d")


def iso_kst(d, hh, mm=0):
    """KST 날짜+시각 -> UTC ISO 문자열 (scan_log 비교용)"""
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class FakeRequest:
    def __init__(self, body=None, headers=None, base_url="http://testserver/"):
        self._body = {} if body is None else body
        self.headers = headers or {}
        self.base_url = base_url

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeConn:
    """재고 마감 기능이 사용하는 SQL만 처리하는 인메모리 커넥션.
    rows는 dict 리스트, execute()는 self를 커서로 반환."""

    def __init__(self):
        self.settings = {}          # key -> value
        self.saves = []             # stock_closing_saves 행 dict
        self.lines = []             # stock_closing_lines 행 dict
        self.scan_log = []          # {branch_code,item_code,scanned_at,scan_type}
        self.inventory = []         # {branch_code,item_code,item_name,quantity}
        self.snapshots = []         # {snap_date,slot,branch_code,item_code,qr_qty,ups_qty}
        self.queries = []           # (sql, params) 실행 기록
        self.committed = False
        self._rows = []
        self._one = None
        self.rowcount = 0
        self._next_id = 1

    # ── 커서 인터페이스 ──
    def execute(self, query, params=None):
        q = " ".join(str(query).split())
        p = list(params or [])
        self.queries.append((q, p))
        self.rowcount = 0
        self._rows = self._dispatch(q, p)
        self._one = self._rows[0] if self._rows else None
        return self

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)

    def commit(self):
        self.committed = True

    def close(self):
        pass

    # ── SQL 디스패치 ──
    def _dispatch(self, q, p):
        # stock_daily_snapshot (스냅샷 크론 — 문자열/파라미터 캡처용)
        if "INSERT INTO stock_daily_snapshot" in q:
            return []
        if "DELETE FROM stock_daily_snapshot" in q:
            cutoff = p[0]
            self.snapshots = [s for s in self.snapshots if not (s["snap_date"] < cutoff)]
            self.rowcount = 1
            return []
        if "DELETE FROM stock_closing_saves" in q:
            cutoff = p[0]
            self.saves = [s for s in self.saves if not (s["close_date"] < cutoff)]
            self.rowcount = 1
            return []
        if "COUNT(*) AS cnt, COUNT(ups_qty) AS ups_cnt FROM stock_daily_snapshot" in q:
            snap_date, slot = p
            rows = [s for s in self.snapshots if s["snap_date"] == snap_date and str(s["slot"]) == str(slot)]
            ups = sum(1 for s in rows if s.get("ups_qty") is not None)
            return [{"cnt": len(rows), "ups_cnt": ups}]
        if "FROM stock_daily_snapshot" in q:
            if "AND branch_code=?" in q:
                snap_date, branch = p
                return [dict(s) for s in self.snapshots
                        if s["snap_date"] == snap_date and s["branch_code"] == branch]
            snap_date = p[0]
            return [dict(s) for s in self.snapshots if s["snap_date"] == snap_date]

        # system_settings
        if "FROM system_settings" in q:
            if "key LIKE ?" in q:
                prefix = p[0].rstrip("%")
                return [{"key": k, "value": v} for k, v in self.settings.items() if k.startswith(prefix)]
            if "key='stock_closing_reminder_last'" in q:
                v = self.settings.get("stock_closing_reminder_last")
                return [{"value": v}] if v is not None else []
            if "WHERE key=?" in q:
                v = self.settings.get(p[0])
                return [{"value": v}] if v is not None else []
            return []
        if "UPDATE system_settings" in q:
            if "key='stock_closing_reminder_last'" in q:
                self.settings["stock_closing_reminder_last"] = p[0]
            else:
                self.settings[p[1]] = p[0]
            self.rowcount = 1
            return []
        if "INSERT INTO system_settings" in q:
            if "'stock_closing_reminder_last'" in q:
                self.settings["stock_closing_reminder_last"] = p[0]
            else:
                self.settings[p[0]] = p[1]
            self.rowcount = 1
            return []

        # stock_closing_lines
        if "DELETE FROM stock_closing_lines" in q:
            sid = p[0]
            self.lines = [l for l in self.lines if l["save_id"] != sid]
            self.rowcount = 1
            return []
        if "INSERT INTO stock_closing_lines" in q:
            self.lines.append({
                "save_id": p[0], "branch_code": p[1], "close_date": p[2],
                "item_code": p[3], "item_name": p[4], "scan_qty": p[5],
                "qr_qty": p[6], "ups_qty": p[7], "counted_qty": p[8], "diff_qty": p[9],
            })
            self.rowcount = 1
            return []
        if "FROM stock_closing_lines" in q:
            sid = p[0]
            rows = [dict(l) for l in self.lines if l["save_id"] == sid]
            rows.sort(key=lambda r: str(r["item_code"]))
            return rows

        # stock_closing_saves
        if "UPDATE stock_closing_saves SET closer_name=?" in q:
            name, sid = p
            for s in self.saves:
                if s["id"] == sid:
                    s["closer_name"] = name
            self.rowcount = 1
            return []
        if "UPDATE stock_closing_saves SET status=?" in q:
            (status, saved_by, saved_at, closed_by, closed_at,
             item_count, mismatch_count, sid) = p
            for s in self.saves:
                if s["id"] == sid:
                    s.update({"status": status, "saved_by": saved_by, "saved_at": saved_at,
                              "closed_by": closed_by, "closed_at": closed_at,
                              "item_count": item_count, "mismatch_count": mismatch_count})
            self.rowcount = 1
            return []
        if "INSERT INTO stock_closing_saves" in q:
            (branch, cdate, status, saved_by, saved_at, closed_by, closed_at,
             item_count, mismatch) = p
            row = {"id": self._next_id, "branch_code": branch, "close_date": cdate,
                   "status": status, "saved_by": saved_by, "saved_at": saved_at,
                   "closed_by": closed_by, "closed_at": closed_at,
                   "item_count": item_count, "mismatch_count": mismatch,
                   "closer_name": None}
            self._next_id += 1
            self.saves.append(row)
            self.rowcount = 1
            return []
        if "FROM stock_closing_saves" in q:
            if "WHERE branch_code=? AND close_date=?" in q:
                branch, cdate = p
                rows = [s for s in self.saves
                        if s["branch_code"] == branch and s["close_date"] == cdate]
            elif "WHERE close_date=?" in q:
                rows = [s for s in self.saves if s["close_date"] == p[0]]
            else:
                rows = list(self.saves)
            if "SELECT *" in q:
                return [dict(s) for s in rows]
            if "SELECT id, status" in q:
                return [{"id": s["id"], "status": s["status"]} for s in rows]
            if q.startswith("SELECT status"):
                return [{"status": s["status"]} for s in rows]
            if "SELECT id FROM" in q:
                return [{"id": s["id"]} for s in rows]
            return [dict(s) for s in rows]

        # scan_log / raw_inventory 집계
        if "MAX(scanned_at)" in q:
            vals = [r["scanned_at"] for r in self.scan_log if r.get("scanned_at")]
            return [{"t": max(vals) if vals else None}]
        if "MAX(uploaded_at)" in q:
            return [{"t": None}]
        if "scan_type IN" in q and "GROUP BY" in q:
            start, end = p[0], p[1]
            branch = p[2] if len(p) > 2 else None
            agg = {}
            for r in self.scan_log:
                if r.get("scan_type") not in ("OUT", "IN"):
                    continue
                if not (start <= str(r["scanned_at"]) < end):
                    continue
                if branch is not None and r["branch_code"] != branch:
                    continue
                k = (r["branch_code"], r["item_code"])
                agg[k] = agg.get(k, 0) + 1
            return [{"branch_code": b, "item_code": i, "cnt": c}
                    for (b, i), c in agg.items()]

        # inventory / raw_inventory
        if "FROM raw_inventory" in q:
            return []
        if "FROM inventory" in q:
            if "WHERE branch_code=?" in q:
                return [dict(r) for r in self.inventory if r["branch_code"] == p[0]]
            return [dict(r) for r in self.inventory]

        return []


class StockClosingTestBase(unittest.TestCase):
    """공통 모킹 셋업 — get_conn/get_session/get_branches/fetch_raw_inventory/발송 함수"""

    BRANCH_USER = {"role": "branch", "branch_code": "A점", "login_id": "a1", "branch_name": "A점"}
    MASTER_USER = {"role": "master", "branch_code": "HQ", "login_id": "admin"}

    def setUp(self):
        self.conn = FakeConn()
        self.session_user = dict(self.BRANCH_USER)
        self.branches = [
            {"branch_code": "A점", "branch_name": "A점", "login_id": "a1"},
            {"branch_code": "B점", "branch_name": "B점", "login_id": "b1"},
            {"branch_code": "본사", "branch_name": "본사", "login_id": "hq"},
            {"branch_code": "남양주점", "branch_name": "남양주점", "login_id": "ny"},
            {"branch_code": "T01", "branch_name": "테스트지점", "login_id": "t1"},
        ]
        self.raw_rows = []
        self.teams_calls = []
        self.teams_result = (True, "성공")
        self.mail_calls = []
        self.mail_result = (True, "")
        self.cron_failures = []

        self._patchers = [
            mock.patch.object(main, "get_conn", side_effect=lambda: self.conn),
            mock.patch.object(main, "get_session", side_effect=lambda token: self.session_user),
            mock.patch.object(main, "get_branches", side_effect=lambda *a, **k: list(self.branches)),
            mock.patch.object(main, "fetch_raw_inventory", side_effect=lambda: list(self.raw_rows)),
            mock.patch.object(main, "send_teams_notification",
                              side_effect=self._fake_teams),
            mock.patch.object(main, "send_email",
                              side_effect=self._fake_email),
            mock.patch.object(main, "report_cron_failure",
                              side_effect=lambda name, detail: self.cron_failures.append((name, detail))),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def _fake_teams(self, branch_code, title, message, link_url="", link_text="", sent_by="", device_info=""):
        self.teams_calls.append({"branch": branch_code, "title": title, "message": message})
        return self.teams_result

    def _fake_email(self, to_addr, subject, body, *a, **k):
        self.mail_calls.append({"to": to_addr, "subject": subject, "body": body})
        return self.mail_result

    # ── 헬퍼 ──
    def seed_items(self, branch="A점", items=("I1", "I2")):
        """scan_log + inventory + raw 재고 기본 데이터 (live 모드용)"""
        for code in items:
            self.conn.scan_log.append({
                "branch_code": branch, "item_code": code,
                "scanned_at": iso_kst(TODAY, 10), "scan_type": "OUT"})
            self.conn.inventory.append({
                "branch_code": branch, "item_code": code,
                "item_name": "상품" + code, "quantity": 100})
            self.raw_rows.append({"branch_code": branch, "item_code": code, "quantity": 90})

    def run_save(self, body, user=None):
        prev = self.session_user
        if user is not None:
            self.session_user = user
        try:
            req = FakeRequest(body=body)
            return asyncio.run(main.stock_closing_save(req, session_token="x"))
        finally:
            self.session_user = prev

    def expect_http(self, status, fn, *a, **k):
        with self.assertRaises(HTTPException) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.status_code, status,
                         "expected %s got %s (%s)" % (status, cm.exception.status_code, cm.exception.detail))
        return cm.exception

    def resp_json(self, resp):
        return json.loads(resp.body.decode("utf-8"))


# ═══════════════ A. 저장·마감 (stock_closing_save) ═══════════════

class TestSave(StockClosingTestBase):

    def test_a1_auth_and_validation(self):
        self.seed_items()
        # 로그인 없음
        self.session_user = None
        self.expect_http(401, self.run_save,
                         {"date": dstr(TODAY), "mode": "draft", "counts": {}})
        self.session_user = dict(self.BRANCH_USER)
        # 마스터 거부
        self.expect_http(403, self.run_save,
                         {"date": dstr(TODAY), "mode": "draft", "counts": {}},
                         user=dict(self.MASTER_USER))
        # 잘못된 모드
        self.expect_http(400, self.run_save,
                         {"date": dstr(TODAY), "mode": "xxx", "counts": {}})
        # 잘못된 날짜 형식
        self.expect_http(400, self.run_save,
                         {"date": "2026-13-99", "mode": "draft", "counts": {}})
        # counts가 dict가 아님
        self.expect_http(400, self.run_save,
                         {"date": dstr(TODAY), "mode": "draft", "counts": "notadict"})
        # 요청 본문 파싱 실패
        req = FakeRequest(body=RuntimeError("bad json"))
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_save(req, session_token="x")))

    def test_a2_editable_dates_only_today_yesterday(self):
        self.seed_items()
        for bad in (DAY_BEFORE, TODAY + timedelta(days=1)):
            self.expect_http(400, self.run_save,
                             {"date": dstr(bad), "mode": "draft", "counts": {}, "closer_name": "x"})
        for ok in (TODAY, YESTERDAY):
            resp = self.run_save({"date": dstr(ok), "mode": "draft", "counts": {}})
            self.assertTrue(self.resp_json(resp)["ok"])

    def test_a3_draft_partial_counts_no_closer(self):
        self.seed_items(items=("I1", "I2", "I3"))
        resp = self.run_save({"date": dstr(TODAY), "mode": "draft",
                              "counts": {"I1": "5"}})  # closer_name 없음, 일부만 입력
        data = self.resp_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "draft")
        save = self.conn.saves[0]
        self.assertEqual(save["status"], "draft")
        self.assertIsNone(save["closed_by"])
        self.assertEqual(save["item_count"], 3)

    def test_a4_close_requires_closer_and_all_counts(self):
        self.seed_items(items=("I1", "I2"))
        # 담당자 없음 -> 거부
        self.expect_http(400, self.run_save,
                         {"date": dstr(TODAY), "mode": "close",
                          "counts": {"I1": "1", "I2": "2"}})
        # 실사 누락 -> 거부
        self.expect_http(400, self.run_save,
                         {"date": dstr(TODAY), "mode": "close", "closer_name": "김마감",
                          "counts": {"I1": "1"}})
        # 모두 입력 -> 성공
        resp = self.run_save({"date": dstr(TODAY), "mode": "close", "closer_name": "김마감",
                              "counts": {"I1": "1", "I2": "2"}})
        data = self.resp_json(resp)
        self.assertTrue(data["ok"])
        save = self.conn.saves[0]
        self.assertEqual(save["status"], "closed")
        self.assertEqual(save["closed_by"], "a1")
        self.assertIsNotNone(save["closed_at"])
        self.assertEqual(save["closer_name"], "김마감")

    def test_a5_already_closed_rejected(self):
        self.seed_items()
        self.conn.saves.append({
            "id": 1, "branch_code": "A점", "close_date": dstr(TODAY), "status": "closed",
            "saved_by": "a1", "saved_at": "x", "closed_by": "a1", "closed_at": "x",
            "item_count": 2, "mismatch_count": 0, "closer_name": "김"})
        for mode in ("draft", "close"):
            body = {"date": dstr(TODAY), "mode": mode, "counts": {"I1": "1", "I2": "2"},
                    "closer_name": "김"}
            self.expect_http(400, self.run_save, body)

    def test_a6_diff_and_mismatch_count(self):
        self.seed_items(items=("I1", "I2"))
        # I1: 실사 95 vs ups 90 -> diff +5 (오차), I2: 실사 90 vs ups 90 -> diff 0
        resp = self.run_save({"date": dstr(TODAY), "mode": "draft",
                              "counts": {"I1": "95", "I2": "90"}})
        self.assertTrue(self.resp_json(resp)["ok"])
        save = self.conn.saves[0]
        self.assertEqual(save["mismatch_count"], 1)  # diff != 0 인 건수
        lines = {l["item_code"]: l for l in self.conn.lines}
        self.assertEqual(lines["I1"]["diff_qty"], 5)
        self.assertEqual(lines["I2"]["diff_qty"], 0)
        # ups가 None이면 diff도 None
        self.raw_rows.clear()
        self.conn2_items = None
        resp = self.run_save({"date": dstr(TODAY), "mode": "draft",
                              "counts": {"I1": "1", "I2": "2"}})
        lines = {l["item_code"]: l for l in self.conn.lines}
        self.assertIsNone(lines["I1"]["diff_qty"])
        self.assertEqual(self.conn.saves[-1]["mismatch_count"], 0)

    def test_a7_closer_name_trim_and_30char_limit(self):
        self.seed_items(items=("I1",))
        long_name = "가" * 40
        resp = self.run_save({"date": dstr(TODAY), "mode": "close", "closer_name": "  " + long_name + "  ",
                              "counts": {"I1": "1"}})
        self.assertTrue(self.resp_json(resp)["ok"])
        self.assertEqual(self.conn.saves[0]["closer_name"], "가" * 30)


# ═══════════════ B. 스냅샷/라이브 아이템 선택 ═══════════════

class TestLiveItems(StockClosingTestBase):

    def _setup_scan(self):
        # A점 I1: 스캔 2건(OUT+IN), I2: 1건, I3: 스캔 있지만 inventory에 없음
        self.conn.scan_log = [
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(TODAY, 9), "scan_type": "OUT"},
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(TODAY, 11), "scan_type": "IN"},
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(TODAY, 12), "scan_type": "ADJUST"},
            {"branch_code": "A점", "item_code": "I2", "scanned_at": iso_kst(TODAY, 10), "scan_type": "OUT"},
            {"branch_code": "A점", "item_code": "I3", "scanned_at": iso_kst(TODAY, 10), "scan_type": "OUT"},
            {"branch_code": "B점", "item_code": "J1", "scanned_at": iso_kst(TODAY, 10), "scan_type": "OUT"},
        ]
        self.conn.inventory = [
            {"branch_code": "A점", "item_code": "I1", "item_name": "상품1", "quantity": 50},
            {"branch_code": "A점", "item_code": "I2", "item_name": "상품2", "quantity": 60},
            {"branch_code": "B점", "item_code": "J1", "item_name": "상품J", "quantity": 70},
        ]
        self.raw_rows = [
            {"branch_code": "A점", "item_code": "I1", "quantity": 40},
            {"branch_code": "A점", "item_code": "I2", "quantity": 55},
            {"branch_code": "B점", "item_code": "J1", "quantity": 65},
        ]
        snap_date = dstr(TODAY)
        # I1: 슬롯 21/22/24 모두 -> 24 사용. I2: 슬롯 21만. J1(B점): 슬롯 22만.
        self.conn.snapshots = [
            {"snap_date": snap_date, "slot": "21", "branch_code": "A점", "item_code": "I1",
             "qr_qty": 1, "ups_qty": 1},
            {"snap_date": snap_date, "slot": "22", "branch_code": "A점", "item_code": "I1",
             "qr_qty": 2, "ups_qty": 2},
            {"snap_date": snap_date, "slot": "24", "branch_code": "A점", "item_code": "I1",
             "qr_qty": 3, "ups_qty": 30},
            {"snap_date": snap_date, "slot": "21", "branch_code": "A점", "item_code": "I2",
             "qr_qty": 4, "ups_qty": 44},
            {"snap_date": snap_date, "slot": "22", "branch_code": "B점", "item_code": "J1",
             "qr_qty": 5, "ups_qty": 55},
        ]

    def test_b1_latest_slot_per_branch(self):
        self._setup_scan()
        items = {it["item_code"]: it for it in main._stock_closing_live_items("A점", TODAY)}
        self.assertEqual(set(items), {"I1", "I2"})  # I3은 inventory에 없어 제외
        i1, i2 = items["I1"], items["I2"]
        self.assertEqual(i1["cnt"], 2)          # OUT+IN만, ADJUST 제외
        self.assertEqual(i1["basis"], "snap")
        self.assertEqual(i1["qr_qty"], 3)       # 슬롯 24 값
        self.assertEqual(i1["ups"], 30)
        self.assertIn("00:15", i1["basis_label"])  # 슬롯24 라벨은 익일 00:15
        # 슬롯 선택은 "지점별" 가장 늦은 슬롯 — A점의 최신은 24이므로 I1의 24만 사용.
        # I2의 슬롯21 스냅샷은 지점 최신 슬롯(24)이 아니라 snap_map에 들어가지 않아 live 처리됨.
        self.assertEqual(i2["basis"], "live")
        self.assertEqual(i2["basis_label"], "현재 값(스냅샷 없음)")
        self.assertEqual(i2["qr_qty"], 60)
        self.assertEqual(i2["ups"], 55)
        # 지점 단위로 보면 B점은 슬롯22가 최신 -> J1은 스냅샷 사용
        all_items = {it["item_code"]: it for it in main._stock_closing_live_items("", TODAY)}
        j1 = all_items["J1"]
        self.assertEqual(j1["basis"], "snap")
        self.assertEqual(j1["qr_qty"], 5)
        self.assertEqual(j1["ups"], 55)

    def test_b2_live_fallback_when_no_snapshot(self):
        self.conn.scan_log = [
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(TODAY, 9), "scan_type": "OUT"}]
        self.conn.inventory = [
            {"branch_code": "A점", "item_code": "I1", "item_name": "상품1", "quantity": 50}]
        self.raw_rows = [{"branch_code": "A점", "item_code": "I1", "quantity": 40}]
        items = main._stock_closing_live_items("A점", TODAY)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["basis"], "live")
        self.assertEqual(it["basis_label"], "현재 값(스냅샷 없음)")
        self.assertEqual(it["qr_qty"], 50)   # inventory 현재값
        self.assertEqual(it["ups"], 40)      # raw_inventory 현재값
        # raw에 없으면 ups=None
        self.raw_rows.clear()
        items = main._stock_closing_live_items("A점", TODAY)
        self.assertIsNone(items[0]["ups"])

    def test_b3_kst_day_boundary(self):
        # sel_date=TODAY: KST 00:00~24:00 = UTC (TODAY-1)15:00 ~ TODAY 15:00
        d = TODAY
        self.conn.scan_log = [
            # KST 23:59 (당일 마지막) -> 포함
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(d, 23, 59), "scan_type": "OUT"},
            # KST 다음날 00:00 -> 제외
            {"branch_code": "A점", "item_code": "I1", "scanned_at": iso_kst(d + timedelta(days=1), 0, 0), "scan_type": "OUT"},
            # KST 당일 00:00 -> 포함
            {"branch_code": "A점", "item_code": "I2", "scanned_at": iso_kst(d, 0, 0), "scan_type": "OUT"},
            # KST 전날 23:59 -> 제외
            {"branch_code": "A점", "item_code": "I2", "scanned_at": iso_kst(d - timedelta(days=1), 23, 59), "scan_type": "OUT"},
        ]
        self.conn.inventory = [
            {"branch_code": "A점", "item_code": "I1", "item_name": "n1", "quantity": 1},
            {"branch_code": "A점", "item_code": "I2", "item_name": "n2", "quantity": 1},
        ]
        items = {it["item_code"]: it for it in main._stock_closing_live_items("A점", d)}
        self.assertEqual(items["I1"]["cnt"], 1)  # 23:59만
        self.assertEqual(items["I2"]["cnt"], 1)  # 00:00만


# ═══════════════ C. 알림 로직 ═══════════════

class TestReminderLogic(StockClosingTestBase):

    def setUp(self):
        super().setUp()
        self.branches.insert(2, {"branch_code": "C점", "branch_name": "C점", "login_id": "c1"})

    def _seed_missing(self):
        """A점=closed, B점=draft, C점=none -> missing은 B,C"""
        self.branches = [
            {"branch_code": "A점", "branch_name": "A점", "login_id": "a1"},
            {"branch_code": "B점", "branch_name": "B점", "login_id": "b1"},
            {"branch_code": "C점", "branch_name": "C점", "login_id": "c1"},
            {"branch_code": "본사", "branch_name": "본사", "login_id": "hq"},
            {"branch_code": "T01", "branch_name": "테스트지점", "login_id": "t1"},
        ]
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": "2020-01-01", "status": "closed",
             "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
            {"id": 2, "branch_code": "B점", "close_date": "2020-01-01", "status": "draft",
             "saved_by": "b", "saved_at": "x", "closed_by": None, "closed_at": None,
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
        ]
        # _sc_missing_branches는 close_date=? 로 조회 — 쿼리 날짜를 saves에 맞춰야 함
        # -> 테스트에서 date_str을 "2020-01-01"로 쓰거나 saves를 해당 날짜로 저장

    def test_c1_missing_branches(self):
        date_str = dstr(TODAY)
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": date_str, "status": "closed",
             "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
            {"id": 2, "branch_code": "B점", "close_date": date_str, "status": "draft",
             "saved_by": "b", "saved_at": "x", "closed_by": None, "closed_at": None,
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
        ]
        missing = main._sc_missing_branches(date_str)
        names = {m["name"] for m in missing}
        codes = {m["code"] for m in missing}
        # A점 closed 제외, B점 draft 포함, C점 none 포함
        self.assertIn("B점", names)
        self.assertIn("C점", names)
        self.assertNotIn("A점", names)
        # 제외 상수(본사)와 "테스트" 포함(테스트지점) 제외
        self.assertNotIn("본사", names)
        self.assertFalse(any("테스트" in n for n in names))
        self.assertNotIn("T01", codes)

    def test_c2_auto_matrix(self):
        date_str = dstr(TODAY)
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": date_str, "status": "closed",
             "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
        ]
        # missing = B점, C점
        combos = [
            # (branch_on, test_on, B토글, C토글) -> 기대 지점발송 대상
            (True,  False, True,  False, ["B점"]),
            (True,  False, False, True,  ["C점"]),
            (False, True,  True,  True,  []),
            (True,  True,  True,  False, ["B점"]),
            (False, False, True,  True,  []),
        ]
        for branch_on, test_on, b_on, c_on, expected in combos:
            self.conn.settings = {
                "stock_closing_notify_branch": "on" if branch_on else "off",
                "stock_closing_notify_test": "on" if test_on else "off",
                "stock_closing_notify_branch:B점": "on" if b_on else "off",
                "stock_closing_notify_branch:C점": "on" if c_on else "off",
            }
            self.teams_calls.clear()
            self.mail_calls.clear()
            res = main._sc_run_reminder(date_str, "http://x", "auto", "by")
            branch_targets = [c["branch"] for c in self.teams_calls
                              if not c["title"].startswith("[테스트")]
            test_calls = [c for c in self.teams_calls if c["title"].startswith("[테스트")]
            self.assertEqual(sorted(branch_targets), sorted(expected),
                             "combo %s" % ((branch_on, test_on, b_on, c_on),))
            if test_on:
                self.assertEqual(len(test_calls), 1)
                self.assertEqual(len(self.mail_calls), 1)
                self.assertTrue(res["test_sent"])
                self.assertTrue(res["mail_sent"])
            else:
                self.assertEqual(test_calls, [])
                self.assertEqual(self.mail_calls, [])
                self.assertIsNone(res["test_sent"])
                self.assertIsNone(res["mail_sent"])
            # 지점 채널 발송 수 = 기대 대상 수
            self.assertEqual(res["branch_sent"], len(expected))

    def test_c2b_no_missing_sends_nothing(self):
        date_str = dstr(TODAY)
        self.conn.settings = {"stock_closing_notify_branch": "on",
                              "stock_closing_notify_test": "on"}
        # saves 없음 -> missing은 전체 지점... 이 아니라 missing이 있으면 발송.
        # missing 없음을 만들려면 모든 지점 closed 필요 -> 간단히 _sc_missing_branches가 빈 목록을
        # 반환하도록 saves에 모든 대상 지점을 closed로 넣는다.
        for b in self.branches:
            if b["branch_code"] in main.STOCK_CLOSING_EXCLUDED_BRANCHES or "테스트" in b["branch_name"]:
                continue
            self.conn.saves.append({
                "id": len(self.conn.saves) + 1, "branch_code": b["branch_code"],
                "close_date": date_str, "status": "closed", "saved_by": "x",
                "saved_at": "x", "closed_by": "x", "closed_at": "x",
                "item_count": 1, "mismatch_count": 0, "closer_name": None})
        self.teams_calls.clear()
        self.mail_calls.clear()
        res = main._sc_run_reminder(date_str, "http://x", "auto", "by")
        self.assertEqual(self.teams_calls, [])
        self.assertEqual(self.mail_calls, [])
        self.assertEqual(res["missing"], [])

    def test_c3_test_mode_ignores_switches(self):
        date_str = dstr(TODAY)
        self.conn.settings = {}  # 스위치 전부 off
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": date_str, "status": "draft",
             "saved_by": "a", "saved_at": "x", "closed_by": None, "closed_at": None,
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
        ]
        self.teams_calls.clear()
        self.mail_calls.clear()
        res = main._sc_run_reminder(date_str, "http://x", "test", "by")
        # 지점 채널 발송 0회, 테스트 채널 1회 + 메일 1회
        branch_targets = [c["branch"] for c in self.teams_calls
                          if c["branch"] != main.STOCK_CLOSING_TEST_BRANCH_CODE]
        self.assertEqual(branch_targets, [])
        test_calls = [c for c in self.teams_calls
                      if c["branch"] == main.STOCK_CLOSING_TEST_BRANCH_CODE]
        self.assertEqual(len(test_calls), 1)
        self.assertTrue(test_calls[0]["title"].startswith("[테스트]"))
        self.assertEqual(len(self.mail_calls), 1)
        self.assertTrue(res["test_sent"])
        self.assertTrue(res["mail_sent"])

    def test_c4_send_and_mail_failures(self):
        date_str = dstr(TODAY)
        self.conn.settings = {"stock_closing_notify_branch": "on",
                              "stock_closing_notify_test": "on",
                              "stock_closing_notify_branch:B점": "on"}
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": date_str, "status": "closed",
             "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
             "item_count": 1, "mismatch_count": 0, "closer_name": None},
        ]
        self.teams_result = (False, "webhook error")
        self.mail_result = (False, "smtp down")
        res = main._sc_run_reminder(date_str, "http://x", "auto", "by")
        self.assertEqual(res["branch_sent"], 0)
        self.assertTrue(len(res["branch_failed"]) >= 1)  # B점 실패
        self.assertFalse(res["test_sent"])
        self.assertFalse(res["mail_sent"])
        self.assertIn("smtp", res.get("mail_detail", ""))


# ═══════════════ D. 크론 ═══════════════

class TestCron(StockClosingTestBase):

    def _cron_req(self, auth=None):
        headers = {}
        if auth is not None:
            headers["authorization"] = auth
        return FakeRequest(headers=headers)

    def _enable_switches(self):
        self.conn.settings = {"stock_closing_notify_branch": "on",
                              "stock_closing_notify_test": "on"}

    def test_d1_auth(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            # 불일치 -> 401
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer wrong")))
            self.assertEqual(resp.status_code, 401)
            # 일치 -> 통과(스위치 off면 skipped 200)
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 200)
            self.assertIn("skipped", self.resp_json(resp))
        # 미설정 -> 인증 생략(현재 동작 고정)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CRON_SECRET", None)
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req(None)))
            self.assertEqual(resp.status_code, 200)

    def test_d2_switches_off_skips(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self.conn.settings = {}
            self.teams_calls.clear()
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            data = self.resp_json(resp)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("skipped", data)
            self.assertEqual(self.teams_calls, [])
            self.assertNotIn("stock_closing_reminder_last", self.conn.settings)

    def test_d3_target_is_kst_yesterday(self):
        # _sc_run_reminder에 전달되는 date_str이 KST 어제인지 확인
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            seen = []
            orig = main._sc_run_reminder
            def spy(date_str, base_url, mode, sent_by):
                seen.append(date_str)
                return {"date": date_str, "mode": mode, "missing": [], "branch_sent": 0,
                        "branch_failed": [], "test_sent": None, "mail_sent": None}
            with mock.patch.object(main, "_sc_run_reminder", side_effect=spy):
                resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(seen, [dstr(YESTERDAY)])

    def test_d4_dedup_same_date(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            self.conn.settings = {"stock_closing_reminder_last": dstr(YESTERDAY)}
            self.teams_calls.clear()
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            data = self.resp_json(resp)
            self.assertIn("skipped", data)
            self.assertEqual(self.teams_calls, [])

    def test_d5_success_writes_marker_200(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            self.conn.settings["stock_closing_notify_branch:B점"] = "on"
            self.conn.saves = [
                {"id": 1, "branch_code": "A점", "close_date": dstr(YESTERDAY), "status": "closed",
                 "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
                 "item_count": 1, "mismatch_count": 0, "closer_name": None},
            ]
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(self.conn.settings.get("stock_closing_reminder_last"), dstr(YESTERDAY))
            self.assertTrue(len(self.teams_calls) >= 1)

    def test_d5b_partial_failure_500_and_report(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            self.conn.settings["stock_closing_notify_branch:B점"] = "on"
            self.conn.saves = [
                {"id": 1, "branch_code": "A점", "close_date": dstr(YESTERDAY), "status": "closed",
                 "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
                 "item_count": 1, "mismatch_count": 0, "closer_name": None},
            ]
            self.teams_result = (False, "fail")
            self.mail_result = (False, "fail")
            resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 500)
            self.assertTrue(any(n == "stock-closing-reminder" for n, _ in self.cron_failures))

    def test_d5c_exception_500(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            with mock.patch.object(main, "_sc_run_reminder", side_effect=RuntimeError("boom")):
                resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 500)
            self.assertTrue(any(n == "stock-closing-reminder" for n, _ in self.cron_failures))
            self.assertNotIn("stock_closing_reminder_last", self.conn.settings)

    def test_d6_exception_during_send_leaves_no_marker(self):
        """발송 도중 예외 -> reminder_last 미기록 (중복 발송 가능성의 현재 동작 고정)"""
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self._enable_switches()
            self.conn.settings["stock_closing_notify_branch:B점"] = "on"
            self.conn.saves = [
                {"id": 1, "branch_code": "A점", "close_date": dstr(YESTERDAY), "status": "closed",
                 "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
                 "item_count": 1, "mismatch_count": 0, "closer_name": None},
            ]
            def boom(*a, **k):
                raise RuntimeError("send exploded")
            with mock.patch.object(main, "send_teams_notification", side_effect=boom):
                resp = asyncio.run(main.cron_stock_closing_reminder(self._cron_req("Bearer s3cret")))
            self.assertEqual(resp.status_code, 500)
            self.assertNotIn("stock_closing_reminder_last", self.conn.settings)


# ═══════════════ E. 수동 요청과 토글 ═══════════════

class TestManualAndToggles(StockClosingTestBase):

    def setUp(self):
        super().setUp()
        self.session_user = dict(self.MASTER_USER)

    def _req(self, body):
        return FakeRequest(body=body, base_url="https://example.test/")

    def test_e1_request_rejections(self):
        date_str = dstr(TODAY)
        # 없는 지점
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "ZZZ", "date": date_str}), session_token="x")))
        # 제외 지점(본사)
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "본사", "date": date_str}), session_token="x")))
        # 테스트 지점
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "T01", "date": date_str}), session_token="x")))
        # 이미 마감
        self.conn.saves.append({
            "id": 1, "branch_code": "A점", "close_date": date_str, "status": "closed",
            "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
            "item_count": 1, "mismatch_count": 0, "closer_name": None})
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x")))
        # 잘못된 날짜
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": "bad"}), session_token="x")))

    def test_e1b_request_switch_combos(self):
        date_str = dstr(TODAY)
        # 스위치 모두 off -> 발송 없이 ok False
        resp = asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        self.assertFalse(data["ok"])
        self.assertEqual(self.teams_calls, [])
        # branch on + 지점 토글 on -> 지점 채널 발송
        self.conn.settings = {"stock_closing_notify_branch": "on",
                              "stock_closing_notify_branch:A점": "on"}
        resp = asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual([c["branch"] for c in self.teams_calls], ["A점"])
        self.assertIn("지점 발송: 성공", data["message"])
        # branch on + 지점 토글 off -> "보내지 않았습니다"
        self.teams_calls.clear()
        self.conn.settings["stock_closing_notify_branch:A점"] = "off"
        resp = asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        self.assertIn("보내지 않았습니다", data["message"])
        self.assertEqual(self.teams_calls, [])
        # test on 추가 -> 테스트 채널 사본
        self.teams_calls.clear()
        self.conn.settings["stock_closing_notify_branch:A점"] = "on"
        self.conn.settings["stock_closing_notify_test"] = "on"
        resp = asyncio.run(main.stock_closing_request(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        branches = [c["branch"] for c in self.teams_calls]
        self.assertIn("A점", branches)
        self.assertIn(main.STOCK_CLOSING_TEST_BRANCH_CODE, branches)
        self.assertIn("테스트 발송: 성공", data["message"])

    def test_e2_request_test_and_reminder_test(self):
        date_str = dstr(TODAY)
        # request-test: 스위치 무관 테스트 채널만
        resp = asyncio.run(main.stock_closing_request_test(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(len(self.teams_calls), 1)
        self.assertEqual(self.teams_calls[0]["branch"], main.STOCK_CLOSING_TEST_BRANCH_CODE)
        # 지점 계정은 403
        self.session_user = dict(self.BRANCH_USER)
        self.expect_http(403, lambda: asyncio.run(main.stock_closing_request_test(
            self._req({"branch_code": "A점", "date": date_str}), session_token="x")))
        self.session_user = dict(self.MASTER_USER)
        # reminder-test: 테스트 채널+메일만
        self.teams_calls.clear()
        self.mail_calls.clear()
        self.conn.saves = [
            {"id": 1, "branch_code": "A점", "close_date": date_str, "status": "draft",
             "saved_by": "a", "saved_at": "x", "closed_by": None, "closed_at": None,
             "item_count": 1, "mismatch_count": 0, "closer_name": None}]
        resp = asyncio.run(main.stock_closing_reminder_test(
            self._req({"date": date_str}), session_token="x"))
        data = self.resp_json(resp)
        self.assertTrue(data["ok"])
        branch_targets = [c["branch"] for c in self.teams_calls
                          if c["branch"] != main.STOCK_CLOSING_TEST_BRANCH_CODE]
        self.assertEqual(branch_targets, [])
        self.assertEqual(len(self.mail_calls), 1)
        self.assertIn("테스트 채널", data["message"])

    def test_e3_toggles(self):
        # notify-toggle: 마스터만, 키 저장
        resp = asyncio.run(main.stock_closing_notify_toggle(
            FakeRequest(body={"key": "branch", "enabled": True}), session_token="x"))
        self.assertTrue(self.resp_json(resp)["ok"])
        self.assertEqual(self.conn.settings["stock_closing_notify_branch"], "on")
        # 잘못된 키 -> 400
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_notify_toggle(
            FakeRequest(body={"key": "bogus", "enabled": True}), session_token="x")))
        # 지점 계정 403
        self.session_user = dict(self.BRANCH_USER)
        self.expect_http(403, lambda: asyncio.run(main.stock_closing_notify_toggle(
            FakeRequest(body={"key": "branch", "enabled": True}), session_token="x")))
        self.session_user = dict(self.MASTER_USER)
        # notify-toggle-branch: 없는 지점 400, 정상 저장
        self.expect_http(400, lambda: asyncio.run(main.stock_closing_notify_toggle_branch(
            FakeRequest(body={"branch_code": "ZZZ", "enabled": True}), session_token="x")))
        resp = asyncio.run(main.stock_closing_notify_toggle_branch(
            FakeRequest(body={"branch_code": "A점", "enabled": True}), session_token="x"))
        self.assertTrue(self.resp_json(resp)["ok"])
        self.assertEqual(self.conn.settings["stock_closing_notify_branch:A점"], "on")
        # notify-toggle-branch-all: 제외 지점·테스트 지점 제외하고 전체 on
        resp = asyncio.run(main.stock_closing_notify_toggle_branch_all(
            FakeRequest(body={"enabled": True}), session_token="x"))
        data = self.resp_json(resp)
        keys = {k for k in self.conn.settings if k.startswith("stock_closing_notify_branch:")}
        self.assertIn("stock_closing_notify_branch:A점", keys)
        self.assertIn("stock_closing_notify_branch:B점", keys)
        self.assertNotIn("stock_closing_notify_branch:본사", keys)
        self.assertNotIn("stock_closing_notify_branch:남양주점", keys)
        self.assertNotIn("stock_closing_notify_branch:T01", keys)
        self.assertIn("전체", data["message"])
        self.assertIn("2개", data["message"])
        # 전체 끄기
        resp = asyncio.run(main.stock_closing_notify_toggle_branch_all(
            FakeRequest(body={"enabled": False}), session_token="x"))
        self.assertEqual(self.conn.settings["stock_closing_notify_branch:A점"], "off")


# ═══════════════ F. 스냅샷 크론 통합 ═══════════════

class TestSnapshotCron(StockClosingTestBase):

    def _req(self, auth="Bearer s3cret"):
        return FakeRequest(headers={"authorization": auth} if auth else {})

    async def _fake_sync(self, slot, target_date=""):
        return {"slot": slot, "target_date": target_date or "x", "inserted": 0}

    def test_f1_snapshot_only_without_date(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            snap_calls = []
            def snap_spy(slot):
                snap_calls.append(slot)
                return {"snap_date": "x", "slot": slot, "rows": 0}
            with mock.patch.object(main, "_take_stock_snapshot", side_effect=snap_spy), \
                 mock.patch.object(main, "_sync_payment_records", side_effect=self._fake_sync):
                # date 없음 -> 스냅샷 실행
                resp = asyncio.run(main.cron_sync_payment_records(self._req(), slot="21", date=""))
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(snap_calls, ["21"])
                self.assertIn("stock_snapshot", self.resp_json(resp))
                # date 지정(수동 재수신) -> 스냅샷 미실행
                snap_calls.clear()
                resp = asyncio.run(main.cron_sync_payment_records(self._req(), slot="21", date="20260101"))
                self.assertEqual(snap_calls, [])
                self.assertNotIn("stock_snapshot", self.resp_json(resp))

    def test_f2_slot24_uses_yesterday_and_retention(self):
        # _take_stock_snapshot 자체 검증: slot 24 -> 전날 날짜, 보관 기준일 = snap_day-100
        from datetime import timedelta as _td
        res = main._take_stock_snapshot("24")
        expected_day = (datetime.now(KST).date() - _td(days=1))
        expected_cutoff = (expected_day - _td(days=100)).strftime("%Y-%m-%d")
        self.assertEqual(res["snap_date"], expected_day.strftime("%Y-%m-%d"))
        self.assertEqual(res["retention_cutoff"], expected_cutoff)
        # 실행된 SQL 캡처: INSERT 파라미터 (snap_date, slot, snapped_at), DELETE cutoff
        ins = [q for q in self.conn.queries if "INSERT INTO stock_daily_snapshot" in q[0]]
        self.assertEqual(len(ins), 1)
        self.assertEqual(ins[0][1][0], expected_day.strftime("%Y-%m-%d"))
        self.assertEqual(ins[0][1][1], "24")
        dels = [q for q in self.conn.queries if "DELETE FROM stock_daily_snapshot" in q[0]]
        self.assertEqual(dels[0][1][0], expected_cutoff)
        dels2 = [q for q in self.conn.queries if "DELETE FROM stock_closing_saves" in q[0]]
        self.assertEqual(dels2[0][1][0], expected_cutoff)
        # slot 21 -> 오늘 날짜
        res = main._take_stock_snapshot("21")
        self.assertEqual(res["snap_date"], TODAY.strftime("%Y-%m-%d"))

    def test_f3_invalid_slot_and_date(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            resp = asyncio.run(main.cron_sync_payment_records(self._req(), slot="99", date=""))
            self.assertEqual(resp.status_code, 400)
            resp = asyncio.run(main.cron_sync_payment_records(self._req(), slot="21", date="2026-1-1"))
            self.assertEqual(resp.status_code, 400)


# ═══════════════ G. 화면 구조 ═══════════════

class TestPages(StockClosingTestBase):

    def _body(self, resp):
        return resp.body.decode("utf-8")

    def test_g1_closing_page_structure(self):
        self.session_user = dict(self.BRANCH_USER)
        resp = asyncio.run(main.stock_closing_page(session_token="x", filter_branch="", date=""))
        html = self._body(resp)
        # 9칸 헤더 + 빈 목록 colspan=9 (아이템 없음 상태)
        self.assertIn("QR 수정", html)
        self.assertIn('colspan="9"', html)
        # 지점 계정 live 모드 -> 마감 담당자 입력칸 활성
        self.assertIn('id="scCloser"', html)
        # 실사 입력칸 width:100%
        # (아이템 없으면 input 없음 -> 별도 시드 후 확인은 생략, 소스 diff로 고정됨)

    def test_g2_closing_page_closed_mode_no_inputs(self):
        self.session_user = dict(self.BRANCH_USER)
        self.conn.saves.append({
            "id": 1, "branch_code": "A점", "close_date": dstr(TODAY), "status": "closed",
            "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
            "item_count": 1, "mismatch_count": 0, "closer_name": "김마감"})
        self.conn.lines.append({
            "save_id": 1, "branch_code": "A점", "close_date": dstr(TODAY), "item_code": "I1",
            "item_name": "상품1", "scan_qty": 3, "qr_qty": 50, "ups_qty": 90,
            "counted_qty": 90, "diff_qty": 0})
        resp = asyncio.run(main.stock_closing_page(session_token="x", filter_branch="", date=""))
        html = self._body(resp)
        self.assertNotIn('class="sc-input"', html)   # 마감 완료 -> 입력칸 없음
        self.assertIn("마감 완료", html)

    def test_g3_master_sees_readonly_closer(self):
        self.session_user = dict(self.MASTER_USER)
        resp = asyncio.run(main.stock_closing_page(session_token="x", filter_branch="A점", date=""))
        html = self._body(resp)
        self.assertNotIn('id="scCloser"', html)      # 마스터는 입력칸 없음
        self.assertIn("마감 담당자", html)

    def test_g4_status_page_structure(self):
        self.session_user = dict(self.MASTER_USER)
        date_str = dstr(TODAY)
        self.conn.saves.append({
            "id": 1, "branch_code": "A점", "close_date": date_str, "status": "closed",
            "saved_by": "a", "saved_at": "x", "closed_by": "a", "closed_at": "x",
            "item_count": 2, "mismatch_count": 1, "closer_name": "김마감"})
        resp = asyncio.run(main.stock_closing_status_page(session_token="x", date=""))
        html = self._body(resp)
        # 7칸 헤더
        for h in ("지점", "상태", "상품 수", "오차 건수", "제출자", "제출 시각", "요청"):
            self.assertIn(h, html)
        self.assertIn("김마감", html)   # closer_name 표시
        # 빈 목록 colspan=7: 지점 목록을 비워 재호출
        self.branches = []
        resp = asyncio.run(main.stock_closing_status_page(session_token="x", date=""))
        html = self._body(resp)
        self.assertIn('colspan="7"', html)


if __name__ == "__main__":
    unittest.main()
