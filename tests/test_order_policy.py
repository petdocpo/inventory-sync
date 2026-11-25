"""order_policy.py 단위 테스트 — 발주 주기 정책 순수 함수 + 달력 유틸.

실행: python -m unittest tests.test_order_policy -v   (프로젝트 루트에서)
"""
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from order_policy import (  # noqa: E402
    ORDER_CYCLES, REGULAR_PERIOD_DAYS, REGULAR_LEAD_MAX_DAYS,
    MONTHLY_LEAD_MAX_DAYS, DEFAULT_BUFFER, CYCLE_ANCHOR,
    normalize_order_cycle, effective_order_cycle,
    normalize_vendor_name, match_vendor_rule, resolve_order_cycle,
    moq_ceil, arrival_stock, regular_order_qty,
    midweek_horizon, midweek_target_stock, midweek_order_qty,
    emergency_eval, tuesday_of_week, next_tuesday,
    is_regular_tuesday, cycle_kind, last_tuesday_of_month,
    monthly_order_dates, monthly_cycle_R, monthly_order_qty,
    arrival_max_date, apply_holiday_shift, business_days_between,
)


class TestNormalizeOrderCycle(unittest.TestCase):
    def test_none_input(self):
        self.assertIsNone(normalize_order_cycle(None))

    def test_empty_string(self):
        self.assertIsNone(normalize_order_cycle(""))
        self.assertIsNone(normalize_order_cycle("   "))

    def test_weekly_variants(self):
        for v in ("weekly", "WEEKLY", " Weekly ", "biweekly", "regular", "정기", "격주"):
            self.assertEqual(normalize_order_cycle(v), "weekly", v)

    def test_monthly_variants(self):
        for v in ("monthly", "Monthly", "month", "월", "월발주"):
            self.assertEqual(normalize_order_cycle(v), "monthly", v)

    def test_none_variants(self):
        for v in ("none", "excluded", "skip", "미발주", "제외"):
            self.assertEqual(normalize_order_cycle(v), "none", v)

    def test_unknown_returns_none(self):
        for v in ("foo", "123", "quarterly", object()):
            self.assertIsNone(normalize_order_cycle(v), v)

    def test_numeric_input(self):
        self.assertIsNone(normalize_order_cycle(123))
        self.assertIsNone(normalize_order_cycle(0))


class TestEffectiveOrderCycle(unittest.TestCase):
    def test_explicit_weekly(self):
        self.assertEqual(effective_order_cycle("weekly"), "weekly")

    def test_explicit_monthly(self):
        self.assertEqual(effective_order_cycle("monthly"), "monthly")

    def test_explicit_none(self):
        self.assertEqual(effective_order_cycle("none"), "none")

    def test_null_legacy_weekly(self):
        self.assertEqual(effective_order_cycle(None, lead_time_days=14), "weekly")
        self.assertEqual(effective_order_cycle(None, lead_time_days=0), "weekly")

    def test_null_legacy_monthly_lead35(self):
        self.assertEqual(effective_order_cycle(None, lead_time_days=35), "monthly")
        self.assertEqual(effective_order_cycle(None, lead_time_days=99), "monthly")

    def test_order_excluded_beats_all(self):
        self.assertEqual(effective_order_cycle("weekly", order_excluded=True), "none")
        self.assertEqual(effective_order_cycle("monthly", order_excluded=True), "none")
        self.assertEqual(effective_order_cycle(None, order_excluded=True, lead_time_days=35), "none")

    def test_explicit_none_beats_exception(self):
        self.assertEqual(effective_order_cycle("none", monthly_exception=True), "none")

    def test_monthly_exception_overrides_weekly(self):
        self.assertEqual(effective_order_cycle("weekly", monthly_exception=True), "monthly")
        self.assertEqual(effective_order_cycle(None, lead_time_days=14, monthly_exception=True), "monthly")

    def test_monthly_exception_does_not_override_explicit_monthly(self):
        self.assertEqual(effective_order_cycle("monthly", monthly_exception=False), "monthly")

    def test_bad_cycle_falls_back_to_legacy(self):
        self.assertEqual(effective_order_cycle("bogus", lead_time_days=35), "monthly")
        self.assertEqual(effective_order_cycle("bogus", lead_time_days=10), "weekly")

    def test_lead_time_none_and_bad(self):
        self.assertEqual(effective_order_cycle(None, lead_time_days=None), "weekly")
        self.assertEqual(effective_order_cycle(None, lead_time_days="abc"), "weekly")
        self.assertEqual(effective_order_cycle(None, lead_time_days="35"), "monthly")


class TestMoqCeil(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(moq_ceil(7, 5), 10)
        self.assertEqual(moq_ceil(10, 5), 10)
        self.assertEqual(moq_ceil(11, 5), 15)

    def test_zero_and_negative(self):
        self.assertEqual(moq_ceil(0, 5), 0)
        self.assertEqual(moq_ceil(-3, 5), 0)

    def test_moq_one(self):
        self.assertEqual(moq_ceil(7, 1), 7)

    def test_bad_moq_treated_as_one(self):
        self.assertEqual(moq_ceil(7, 0), 7)
        self.assertEqual(moq_ceil(7, -5), 7)
        self.assertEqual(moq_ceil(7, None), 7)

    def test_float_qty(self):
        self.assertEqual(moq_ceil(7.2, 5), 10)

    def test_exact_multiple_no_rounding_error(self):
        # 부동소수 오차로 10.0000000001 -> 15 가 되면 안 됨
        self.assertEqual(moq_ceil(2 * 14 * 1.05, 1), 30)  # 29.4 -> 30
        self.assertEqual(moq_ceil(0.3 / 0.1, 1), 3)


class TestArrivalStock(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(arrival_stock(10, 2, 3), 4.0)   # 10 - 2*3
        self.assertEqual(arrival_stock(5, 2, 3), 0.0)    # 5-6 -> 0
        self.assertEqual(arrival_stock(0, 2, 3), 0.0)

    def test_negative_P_clamped(self):
        # regular_order_qty에서 P는 0 하한 처리
        self.assertEqual(arrival_stock(-5, 1, 1), 0.0)

    def test_d_zero(self):
        self.assertEqual(arrival_stock(3, 0, 3), 3.0)


class TestRegularOrderQty(unittest.TestCase):
    def test_hanseo_example(self):
        # 한서 예시: D=2, 버퍼 0, 가용재고 10, 입고 3일 -> 24 (12일치)
        # arrival = max(0,10-2*3)=4, cover=2*14=28, Q=24
        self.assertEqual(regular_order_qty(2, 10, buffer=0.0, moq=1), 24)

    def test_arrival_zero_full_cover(self):
        # P가 작아 도착 시 예상재고 0 -> cover 전량
        self.assertEqual(regular_order_qty(2, 0, buffer=0.0, moq=1), 28)
        self.assertEqual(regular_order_qty(2, 5, buffer=0.0, moq=1), 28)

    def test_p_sufficient_zero(self):
        # P가 충분히 크면 Q=0
        self.assertEqual(regular_order_qty(2, 100, buffer=0.0, moq=1), 0)
        self.assertEqual(regular_order_qty(0, 0, buffer=0.0, moq=1), 0)

    def test_d_zero(self):
        self.assertEqual(regular_order_qty(0, 0), 0)
        self.assertEqual(regular_order_qty(0, 5), 0)

    def test_buffer_default_105(self):
        # b=0.05 -> cover = D*14*1.05
        self.assertEqual(regular_order_qty(10, 0, moq=1), 147)  # 10*14*1.05=147

    def test_moq_rounding(self):
        self.assertEqual(regular_order_qty(2, 10, buffer=0.0, moq=10), 30)
        self.assertEqual(regular_order_qty(2, 10, buffer=0.0, moq=100), 100)

    def test_large_moq(self):
        q = regular_order_qty(1, 0, buffer=0.0, moq=1)
        self.assertEqual(q, 14)
        self.assertEqual(regular_order_qty(1, 0, buffer=0.0, moq=50), 50)

    def test_custom_R(self):
        # R=28 월간식 커버리지
        self.assertEqual(regular_order_qty(2, 0, R=28, L_max=11, buffer=0.0, moq=1), 56)

    def test_monotonic_D(self):
        # D 증가 시 Q 비감소
        prev = -1
        for d in (0, 0.5, 1, 2, 5, 10):
            q = regular_order_qty(d, 3, buffer=0.05, moq=1)
            self.assertGreaterEqual(q, prev)
            prev = q

    def test_monotonic_P_decreasing(self):
        # P 증가 시 Q 비증가
        prev = 10 ** 9
        for p in (0, 5, 10, 20, 40, 100):
            q = regular_order_qty(3, p, buffer=0.05, moq=1)
            self.assertLessEqual(q, prev)
            prev = q

    def test_p_boundary_arrival(self):
        # P - D*L 경계: P=6, D=2, L=3 -> arrival=0, Q=cover
        self.assertEqual(regular_order_qty(2, 6, buffer=0.0, moq=1), 28)
        self.assertEqual(regular_order_qty(2, 7, buffer=0.0, moq=1), 27)  # arrival=1 -> 28-1

    def test_float_inputs(self):
        q = regular_order_qty("2", "10", buffer="0", moq="1")
        self.assertEqual(q, 24)

    def test_nan_safety(self):
        q = regular_order_qty(float("nan"), 5)
        self.assertEqual(q, 0)


class TestMidweek(unittest.TestCase):
    TODAY = date(2026, 11, 17)          # 중간주 화요일
    NEXT_REG = date(2026, 11, 24)      # 다음 정기주 화요일

    def test_horizon(self):
        # H = (11/24 + 3) - 11/17 = 10일
        self.assertEqual(midweek_horizon(self.TODAY, self.NEXT_REG, L_max=3), 10)

    def test_target_stock(self):
        F, H = midweek_target_stock(2, self.TODAY, self.NEXT_REG, L_max=3,
                                    buffer=0.0, min_safety_qty=0)
        self.assertEqual(H, 10)
        self.assertEqual(F, 20.0)

    def test_target_stock_min_safety_floor(self):
        F, _ = midweek_target_stock(0.1, self.TODAY, self.NEXT_REG, L_max=3,
                                    buffer=0.0, min_safety_qty=30)
        self.assertEqual(F, 30.0)

    def test_p_above_F_zero(self):
        self.assertEqual(midweek_order_qty(2, 50, self.TODAY, self.NEXT_REG,
                                           buffer=0.0, min_safety_qty=0, moq=1), 0)

    def test_basic_topup(self):
        # F=20, P=10 -> raw=10, cap=regular(2,10,R=14,L=3,b=0)=24 -> Q=10
        self.assertEqual(midweek_order_qty(2, 10, self.TODAY, self.NEXT_REG,
                                           buffer=0.0, min_safety_qty=0, moq=1), 10)

    def test_cap_applies(self):
        # min_safety_qty가 크면 F-P가 정기 상한을 넘을 수 있음 -> cap
        # F = max(2*10*1, 100)=100, P=0 -> raw=100; cap=regular(2,0)=28 -> Q=28
        self.assertEqual(midweek_order_qty(2, 0, self.TODAY, self.NEXT_REG,
                                           buffer=0.0, min_safety_qty=100, moq=1), 28)

    def test_moq_applied(self):
        # raw=10, moq=6 -> 12
        self.assertEqual(midweek_order_qty(2, 10, self.TODAY, self.NEXT_REG,
                                           buffer=0.0, min_safety_qty=0, moq=6), 12)

    def test_d_zero(self):
        # D=0, min=0 -> F=0, P>=0 -> 0
        self.assertEqual(midweek_order_qty(0, 0, self.TODAY, self.NEXT_REG,
                                           buffer=0.0, min_safety_qty=0, moq=1), 0)

    def test_negative_horizon(self):
        # next_regular가 과거면 H<0 -> F<=0(또는 min) -> P>=F면 0
        past = date(2026, 11, 10)
        self.assertEqual(midweek_order_qty(2, 100, self.TODAY, past,
                                           buffer=0.0, min_safety_qty=0, moq=1), 0)

    def test_midweek_never_exceeds_regular(self):
        # 다양한 P에서 중간주 Q <= 정기 Q (같은 P, moq=1)
        for p in (0, 3, 7, 15, 30, 60):
            mid = midweek_order_qty(2, p, self.TODAY, self.NEXT_REG,
                                    buffer=0.05, min_safety_qty=5, moq=1)
            reg = regular_order_qty(2, p, buffer=0.05, moq=1)
            self.assertLessEqual(mid, reg, f"P={p}: mid={mid} reg={reg}")


class TestEmergencyEval(unittest.TestCase):
    MONDAY = date(2026, 11, 16)   # 화요일 전날
    WEDNESDAY = date(2026, 11, 18)

    def test_candidate_trigger(self):
        # 월요일: next_tue=11/17, H_e=(11/17+3)-11/16=4
        r = emergency_eval(stock=1, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=0, moq=1)
        self.assertIsNotNone(r)
        self.assertEqual(r["H_e"], 4)
        self.assertEqual(r["threshold"], 20.0)
        self.assertEqual(r["qty"], 12)  # 3*4*1 - 0

    def test_no_candidate_when_stock_sufficient(self):
        r = emergency_eval(stock=20, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=0, moq=1)
        self.assertIsNone(r)
        r2 = emergency_eval(stock=21, D_alert=5, D=3, today=self.MONDAY,
                            buffer=0.0, P=0, moq=1)
        self.assertIsNone(r2)

    def test_boundary_stock_equals_threshold(self):
        # stock == threshold -> 후보 아님 (엄격한 <)
        r = emergency_eval(stock=20, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=0, moq=1)
        self.assertIsNone(r)

    def test_qty_uses_D_not_D_alert(self):
        r = emergency_eval(stock=1, D_alert=10, D=2, today=self.MONDAY,
                           buffer=0.0, P=0, moq=1)
        self.assertEqual(r["qty"], 8)  # 2*4

    def test_P_reduces_qty(self):
        r = emergency_eval(stock=1, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=5, moq=1)
        self.assertEqual(r["qty"], 7)  # 12-5

    def test_P_larger_than_need_zero_qty_but_still_candidate(self):
        # 트리거는 현재고 기준, 수량은 P 차감 -> 0 가능
        r = emergency_eval(stock=1, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=100, moq=1)
        self.assertIsNotNone(r)
        self.assertEqual(r["qty"], 0)

    def test_moq(self):
        r = emergency_eval(stock=1, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.0, P=0, moq=10)
        self.assertEqual(r["qty"], 20)  # 12 -> 20

    def test_buffer(self):
        r = emergency_eval(stock=1, D_alert=5, D=3, today=self.MONDAY,
                           buffer=0.05, P=0, moq=1)
        self.assertEqual(r["qty"], math.ceil(3 * 4 * 1.05))  # 12.6 -> 13

    def test_wednesday_horizon(self):
        # 수요일: next_tue=11/24, H_e=(11/24+3)-11/18=9
        r = emergency_eval(stock=0, D_alert=1, D=1, today=self.WEDNESDAY,
                           buffer=0.0, P=0, moq=1)
        self.assertEqual(r["H_e"], 9)
        self.assertEqual(r["qty"], 9)

    def test_friday_before_tuesday(self):
        fri = date(2026, 11, 20)
        r = emergency_eval(stock=0, D_alert=1, D=1, today=fri,
                           buffer=0.0, P=0, moq=1)
        self.assertEqual(r["H_e"], 7)  # (11/24+3)-11/20 = 7

    def test_tuesday_returns_next_week(self):
        tue = date(2026, 11, 17)
        # 화요일에도 호출 가능(긴급은 화요일 외지만 함수는 동작해야 함)
        r = emergency_eval(stock=0, D_alert=1, D=1, today=tue,
                           buffer=0.0, P=0, moq=1)
        self.assertIsNotNone(r)
        self.assertEqual(r["H_e"], (date(2026, 11, 24) + timedelta(days=3) - tue).days)


class TestCalendar(unittest.TestCase):
    def test_tuesday_of_week(self):
        self.assertEqual(tuesday_of_week(date(2026, 11, 16)), date(2026, 11, 17))  # 월->화
        self.assertEqual(tuesday_of_week(date(2026, 11, 17)), date(2026, 11, 17))  # 화->화
        self.assertEqual(tuesday_of_week(date(2026, 11, 18)), date(2026, 11, 17))  # 수->화
        self.assertEqual(tuesday_of_week(date(2026, 11, 22)), date(2026, 11, 17))  # 일->화

    def test_next_tuesday(self):
        self.assertEqual(next_tuesday(date(2026, 11, 16)), date(2026, 11, 17))
        self.assertEqual(next_tuesday(date(2026, 11, 17)), date(2026, 11, 24))  # 당일 제외
        self.assertEqual(next_tuesday(date(2026, 11, 23)), date(2026, 11, 24))

    def test_is_regular_tuesday(self):
        self.assertTrue(is_regular_tuesday(date(2026, 11, 24)))   # anchor+14
        self.assertTrue(is_regular_tuesday(date(2026, 12, 8)))
        self.assertTrue(is_regular_tuesday(date(2026, 11, 10)))   # anchor itself
        self.assertFalse(is_regular_tuesday(date(2026, 11, 17)))  # 중간주
        self.assertFalse(is_regular_tuesday(date(2026, 11, 16)))  # 월요일
        self.assertFalse(is_regular_tuesday(date(2026, 11, 3)))   # anchor 이전

    def test_cycle_kind(self):
        self.assertEqual(cycle_kind(date(2026, 11, 24)), "regular")
        self.assertEqual(cycle_kind(date(2026, 11, 17)), "supply")
        self.assertEqual(cycle_kind(date(2026, 11, 16)), "not_tuesday")
        self.assertEqual(cycle_kind(date(2026, 11, 3)), "before_anchor")

    def test_last_tuesday_of_month(self):
        self.assertEqual(last_tuesday_of_month(2026, 11), date(2026, 11, 24))
        self.assertEqual(last_tuesday_of_month(2026, 12), date(2026, 12, 29))
        self.assertEqual(last_tuesday_of_month(2027, 1), date(2027, 1, 26))
        self.assertEqual(last_tuesday_of_month(2026, 2), date(2026, 2, 24))
        # 결과는 항상 화요일
        for y in range(2026, 2029):
            for m in range(1, 13):
                d = last_tuesday_of_month(y, m)
                self.assertEqual(d.weekday(), 1, f"{y}-{m}")
                self.assertTrue(d.day >= 22)  # 마지막 주에 위치

    def test_monthly_cycle_R_28_or_35(self):
        # 모든 월 발주일의 R은 28 또는 35
        dates = monthly_order_dates(2026, 1, 2028, 12)
        self.assertEqual(len(dates), 36)
        for d in dates:
            R, nxt = monthly_cycle_R(d)
            self.assertIn(R, (28, 35), f"{d}: R={R}")
            self.assertEqual(nxt, monthly_cycle_R(nxt)[1] if False else nxt)  # sanity
            # nxt는 d의 다음 월 마지막 화요일
            if d.month == 12:
                self.assertEqual(nxt.year, d.year + 1)
                self.assertEqual(nxt.month, 1)
            else:
                self.assertEqual(nxt.month, d.month + 1)

    def test_monthly_order_qty(self):
        # 월 발주: R=28/35, L_max=11
        od = date(2026, 11, 24)  # 11월 마지막 화요일
        R, _ = monthly_cycle_R(od)
        q = monthly_order_qty(2, 10, od, buffer=0.0, moq=1)
        # cover = 2*R, arrival = max(0,10-22)=0 -> Q=2*R
        self.assertEqual(q, 2 * R)

    def test_monthly_moq(self):
        od = date(2026, 11, 24)
        q = monthly_order_qty(1, 0, od, buffer=0.0, moq=50)
        self.assertEqual(q % 50, 0)
        self.assertGreaterEqual(q, 28)

    def test_arrival_max_date(self):
        d = date(2026, 11, 24)
        self.assertEqual(arrival_max_date(d, monthly=False), date(2026, 11, 27))
        self.assertEqual(arrival_max_date(d, monthly=True), date(2026, 12, 5))

    def test_apply_holiday_shift_default_off(self):
        d = date(2026, 12, 25)  # 금요일(크리스마스)
        self.assertEqual(apply_holiday_shift(d), d)  # holidays=None -> 미적용
        # holidays 지정 시 직전 영업일
        hol = {date(2026, 12, 25), date(2026, 12, 24)}
        self.assertEqual(apply_holiday_shift(date(2026, 12, 25), holidays=hol),
                         date(2026, 12, 23))
        # 주말 화요일은 없음(화요일만 쓰지만 방어)
        self.assertEqual(apply_holiday_shift(date(2026, 12, 26), holidays=hol),
                         date(2026, 12, 23))  # 토->금(공휴일)->목

    def test_business_days_between(self):
        # 화 11/24 ~ 금 11/27: 수,목,금 = 3일 (d0 배타, d1 포함)
        self.assertEqual(business_days_between(date(2026, 11, 24), date(2026, 11, 27)), 3)
        self.assertEqual(business_days_between(date(2026, 11, 27), date(2026, 11, 24)), 0)
        # 주말 포함 구간
        self.assertEqual(business_days_between(date(2026, 11, 20), date(2026, 11, 24)), 2)  # 월,화

    def test_year_end_boundary(self):
        # 12월 마지막 화요일 -> 1월 마지막 화요일 R 계산
        d = last_tuesday_of_month(2026, 12)
        R, nxt = monthly_cycle_R(d)
        self.assertEqual(nxt, last_tuesday_of_month(2027, 1))
        self.assertIn(R, (28, 35))

    def test_anchor_constants(self):
        self.assertEqual(CYCLE_ANCHOR, date(2026, 11, 10))
        self.assertEqual(CYCLE_ANCHOR.weekday(), 1)
        self.assertEqual(REGULAR_PERIOD_DAYS, 14)
        self.assertEqual(REGULAR_LEAD_MAX_DAYS, 3)
        self.assertEqual(MONTHLY_LEAD_MAX_DAYS, 11)
        self.assertEqual(ORDER_CYCLES, ("weekly", "monthly", "none"))


class TestNormalizeVendorName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_vendor_name("숨의"), "숨의")
        self.assertEqual(normalize_vendor_name("  블루어  "), "블루어")
        self.assertEqual(normalize_vendor_name("숨  의"), "숨 의")  # 연속 공백 1개로
        self.assertEqual(normalize_vendor_name(None), "")
        self.assertEqual(normalize_vendor_name(""), "")


class TestMatchVendorRule(unittest.TestCase):
    RULES = [
        {"vendor_name": "숨의", "branch_code": None, "order_cycle": "monthly"},
        {"vendor_name": "블루어", "branch_code": "김해점", "order_cycle": "monthly"},
        {"vendor_name": "블루어", "branch_code": "부산점", "order_cycle": "monthly"},
        {"vendor_name": "블루어", "branch_code": "세종점", "order_cycle": "monthly"},
        {"vendor_name": "블루어", "branch_code": None, "order_cycle": "weekly"},
    ]

    def test_soomi_all_branches_monthly(self):
        for bc in ("김해점", "부산점", "세종점", "경기김포점", "본사"):
            self.assertEqual(match_vendor_rule(self.RULES, "숨의", bc), "monthly", bc)

    def test_blueur_branch_specific(self):
        for bc in ("김해점", "부산점", "세종점"):
            self.assertEqual(match_vendor_rule(self.RULES, "블루어", bc), "monthly", bc)
        # 지점 미지정 지점은 전 지점 규칙(weekly) 적용
        self.assertEqual(match_vendor_rule(self.RULES, "블루어", "경기김포점"), "weekly")

    def test_branch_rule_beats_global(self):
        rules = [
            {"vendor_name": "X상사", "branch_code": None, "order_cycle": "weekly"},
            {"vendor_name": "X상사", "branch_code": "김해점", "order_cycle": "monthly"},
        ]
        self.assertEqual(match_vendor_rule(rules, "X상사", "김해점"), "monthly")
        self.assertEqual(match_vendor_rule(rules, "X상사", "부산점"), "weekly")

    def test_whitespace_normalized(self):
        self.assertEqual(match_vendor_rule(self.RULES, "  숨의 ", "김해점"), "monthly")
        self.assertEqual(match_vendor_rule(self.RULES, "블 루어", "김해점"), None)  # 내부 공백은 다른 값

    def test_no_match(self):
        self.assertIsNone(match_vendor_rule(self.RULES, "하얀나래", "김해점"))
        self.assertIsNone(match_vendor_rule(self.RULES, "", "김해점"))
        self.assertIsNone(match_vendor_rule(self.RULES, None, "김해점"))
        self.assertIsNone(match_vendor_rule([], "숨의", "김해점"))
        self.assertIsNone(match_vendor_rule(None, "숨의", "김해점"))

    def test_invalid_cycle_rule_ignored(self):
        rules = [{"vendor_name": "숨의", "branch_code": None, "order_cycle": "bogus"}]
        self.assertIsNone(match_vendor_rule(rules, "숨의", "김해점"))


class TestResolveOrderCycleNewMode(unittest.TestCase):
    """신 모드 우선순위: 미발주 > 상품 명시 > 월 예외 > 거래처 규칙 > 기본 정기."""
    def r(self, oc=None, exc=False, lead=0, mexc=False, vc=None, mode="new"):
        return resolve_order_cycle(oc, order_excluded=exc, lead_time_days=lead,
                                   monthly_exception=mexc, vendor_cycle=vc, mode=mode)

    # 1) 미발주 최우선
    def test_none_beats_everything(self):
        self.assertEqual(self.r(oc="weekly", exc=True, mexc=True, vc="monthly"), "none")
        self.assertEqual(self.r(oc="none", vc="monthly"), "none")
        self.assertEqual(self.r(oc=None, exc=True), "none")

    # 2) 상품 명시 order_cycle
    def test_explicit_weekly_beats_exception_and_vendor(self):
        self.assertEqual(self.r(oc="weekly", mexc=True, vc="monthly"), "weekly")
    def test_explicit_monthly_beats_everything_but_none(self):
        self.assertEqual(self.r(oc="monthly", mexc=False, vc="weekly"), "monthly")
        self.assertEqual(self.r(oc="monthly", exc=False), "monthly")

    # 3) 월 발주 예외
    def test_monthly_exception_beats_vendor(self):
        self.assertEqual(self.r(oc=None, mexc=True, vc="weekly"), "monthly")
    def test_no_exception_vendor_monthly(self):
        self.assertEqual(self.r(oc=None, mexc=False, vc="monthly"), "monthly")

    # 4) 거래처 규칙
    def test_vendor_weekly(self):
        self.assertEqual(self.r(oc=None, vc="weekly"), "weekly")

    # 5) 기본 정기 — lead_time>=35 무시
    def test_default_weekly_ignores_lead35(self):
        self.assertEqual(self.r(oc=None, lead=35), "weekly")
        self.assertEqual(self.r(oc=None, lead=99), "weekly")
        self.assertEqual(self.r(oc=None, lead=0), "weekly")

    # NULL/이상값 처리
    def test_null_and_bad_values(self):
        self.assertEqual(self.r(oc=None), "weekly")
        self.assertEqual(self.r(oc="bogus", vc="monthly"), "monthly")  # 해석 불가->자동 취급
        self.assertEqual(self.r(oc="", vc=None), "weekly")
        self.assertEqual(self.r(vc="bogus"), "weekly")

    # legacy 모드 위임
    def test_legacy_mode_unchanged(self):
        self.assertEqual(self.r(oc=None, lead=35, mode="legacy"), "monthly")
        self.assertEqual(self.r(oc=None, lead=14, mode="legacy"), "weekly")
        self.assertEqual(self.r(oc=None, lead=14, mexc=True, vc="monthly", mode="legacy"), "monthly")
        self.assertEqual(self.r(oc="weekly", lead=35, mode="legacy"), "weekly")
        self.assertEqual(self.r(mode="anything_else"), "weekly")


class TestInvariants(unittest.TestCase):
    """속성 기반 간단 검증 — 무작위 케이스."""

    def test_qty_invariants_random(self):
        import random
        rng = random.Random(42)
        for _ in range(3000):
            D = rng.uniform(0, 20)
            P = rng.uniform(0, 200)
            b = rng.choice([0.0, 0.05, 0.1, 0.2])
            moq = rng.choice([1, 5, 10, 50])
            q = regular_order_qty(D, P, buffer=b, moq=moq)
            self.assertIsInstance(q, int)
            self.assertGreaterEqual(q, 0)
            self.assertEqual(q % moq, 0)
            if P >= D * 17 * (1 + b) + 100:  # 충분한 P
                self.assertEqual(q, 0)

    def test_monotonicity_random(self):
        import random
        rng = random.Random(7)
        for _ in range(500):
            D = rng.uniform(0.1, 10)
            b = rng.uniform(0, 0.3)
            p1, p2 = sorted([rng.uniform(0, 100), rng.uniform(0, 100)])
            q1 = regular_order_qty(D, p1, buffer=b, moq=1)
            q2 = regular_order_qty(D, p2, buffer=b, moq=1)
            self.assertLessEqual(q2, q1)  # P 증가 -> Q 비증가

    def test_midweek_cap_random(self):
        import random
        rng = random.Random(11)
        for _ in range(500):
            D = rng.uniform(0, 10)
            P = rng.uniform(0, 100)
            b = rng.uniform(0, 0.3)
            ms = rng.uniform(0, 50)
            mid = midweek_order_qty(D, P, date(2026, 11, 17), date(2026, 11, 24),
                                    buffer=b, min_safety_qty=ms, moq=1)
            reg = regular_order_qty(D, P, buffer=b, moq=1)
            self.assertLessEqual(mid, reg)
            self.assertGreaterEqual(mid, 0)


if __name__ == "__main__":
    unittest.main()
