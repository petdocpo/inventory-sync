"""
order_policy.py — 발주 주기 정책 순수 함수 + 발주 달력 유틸.

설계 v2(확정 공식) 구현. 어떤 화면·크론에도 연결하지 않는 독립 모듈.
- 정기 화요일: 도착 시 예상재고 = max(0, P - D*L_max), Q = max(0, D*R*(1+b) - 예상재고) -> MOQ 올림
- 중간 화요일 보충: H=(다음 정기 발주일+L_max)-오늘, F=max(D*H*(1+b), min_safety),
  P<F 이면 Q=F-P, 상한은 같은 P로 계산한 정기 수량
- 긴급(화요일 외): 현재고 < D_alert*H_e 이면 후보, Q=max(0, D*H_e*(1+b)-P)
  (H_e=(다음 화요일+3일)-오늘, D_alert=max(D_장기, D_최근14일))
- 리드타임은 입력값이 아니라 상수: 정기 R=14, L_max=3 / 월간 R=28|35, L_max=11
- 발주 방식: order_cycle('weekly'|'monthly'|'none'), NULL이면 레거시 규칙으로 해석

주의: 이 모듈은 DB·네트워크에 접근하지 않는다. 모든 함수는 순수 함수.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

# ── 상수 ──────────────────────────────────────────────
ORDER_CYCLES = ("weekly", "monthly", "none")
REGULAR_PERIOD_DAYS = 14      # 격주 정기 발주 주기 R
REGULAR_LEAD_MAX_DAYS = 3     # 화요일 발주 -> 금요일 입고 최대 소요일
MONTHLY_LEAD_MAX_DAYS = 11    # 월 발주 -> 입고 최대 소요일
DEFAULT_BUFFER = 0.05         # b (현행 안전계수 1.05 -> 버퍼율 5%)
CYCLE_ANCHOR = date(2026, 11, 10)  # 정기주 기준일(화요일)
EMERGENCY_ARRIVAL_DAYS = 3    # 긴급 알림의 "다음 화요일+3일" 입고 가정


# ── 발주 방식 해석 ────────────────────────────────────
def normalize_order_cycle(value):
    """임의 입력을 'weekly'|'monthly'|'none'|None 으로 정규화. 알 수 없는 값은 None."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("weekly", "biweekly", "regular", "정기", "격주"):
        return "weekly"
    if v in ("monthly", "month", "월", "월발주"):
        return "monthly"
    if v in ("none", "excluded", "skip", "미발주", "제외"):
        return "none"
    return None


def effective_order_cycle(order_cycle, order_excluded=False, lead_time_days=0,
                          monthly_exception=False):
    """실효 발주 방식. 우선순위:
    1) order_excluded 또는 order_cycle='none' -> 'none' (미발주)
    2) 지점 월 발주 예외 -> 'monthly' (상품 설정보다 우선)
    3) order_cycle 명시값
    4) 레거시: lead_time_days>=35 -> 'monthly', 그 외 'weekly'
    """
    if order_excluded:
        return "none"
    oc = normalize_order_cycle(order_cycle)
    if oc == "none":
        return "none"
    if monthly_exception:
        return "monthly"
    if oc in ("weekly", "monthly"):
        return oc
    try:
        lead = int(lead_time_days or 0)
    except (TypeError, ValueError):
        lead = 0
    return "monthly" if lead >= 35 else "weekly"


# ── 수량 계산 (순수 함수) ──────────────────────────────
def _f(x):
    """float 변환, 실패/None -> 0.0"""
    try:
        v = float(x)
        return v if v == v else 0.0  # NaN 방지
    except (TypeError, ValueError):
        return 0.0


def moq_ceil(qty, moq):
    """수량을 MOQ 배수로 올림. moq<=0 또는 None 이면 1 취급. qty<0 -> 0."""
    q = _f(qty)
    if q <= 0:
        return 0
    m = _f(moq)
    if m <= 0:
        m = 1
    return int(math.ceil(round(q / m, 9))) * int(m) if m == int(m) else int(math.ceil(q / m)) * int(m)


def arrival_stock(P, D, L_max):
    """도착 시 예상재고 = max(0, P - D*L_max). 발주~입고 최대 소요일 동안의 예상 판매를 뺀다."""
    return max(0.0, _f(P) - _f(D) * _f(L_max))


def regular_order_qty(D, P, R=REGULAR_PERIOD_DAYS, L_max=REGULAR_LEAD_MAX_DAYS,
                      buffer=DEFAULT_BUFFER, moq=1):
    """정기 화요일 발주 수량.
    Q = max(0, D*R*(1+b) - max(0, P - D*L_max)) -> MOQ 올림.
    반환: MOQ 적용 후 정수 수량.
    """
    D = _f(D); P = max(0.0, _f(P))
    cover = D * _f(R) * (1.0 + _f(buffer))
    need = cover - arrival_stock(P, D, L_max)
    return moq_ceil(max(0.0, need), moq)


def midweek_horizon(today, next_regular_date, L_max=REGULAR_LEAD_MAX_DAYS):
    """중간주 보충 수요 기간 H = (다음 정기 발주일 + L_max) - 오늘 (일수)."""
    h = (next_regular_date + timedelta(days=int(L_max))) - today
    return h.days


def midweek_target_stock(D, today, next_regular_date, L_max=REGULAR_LEAD_MAX_DAYS,
                         buffer=DEFAULT_BUFFER, min_safety_qty=0):
    """중간주 보충선 F = max(D*H*(1+b), min_safety_qty)."""
    H = midweek_horizon(today, next_regular_date, L_max)
    return max(_f(D) * H * (1.0 + _f(buffer)), _f(min_safety_qty)), H


def midweek_order_qty(D, P, today, next_regular_date, L_max=REGULAR_LEAD_MAX_DAYS,
                      buffer=DEFAULT_BUFFER, min_safety_qty=0, moq=1,
                      R=REGULAR_PERIOD_DAYS):
    """중간 화요일 보충 발주 수량.
    P < F 이면 Q = F - P, 상한은 같은 P로 계산한 정기 수량.
    """
    P = max(0.0, _f(P))
    F, _H = midweek_target_stock(D, today, next_regular_date, L_max, buffer, min_safety_qty)
    if P >= F:
        return 0
    raw = F - P
    cap = regular_order_qty(D, P, R=R, L_max=L_max, buffer=buffer, moq=1)
    # 상한은 MOQ 적용 전 정규 수량 기준으로 비교 후 올림
    return moq_ceil(min(raw, cap), moq)


def emergency_eval(stock, D_alert, D, today, buffer=DEFAULT_BUFFER, P=0.0, moq=1):
    """긴급 발주 알림 후보 평가.
    조건: 현재고 < D_alert * H_e, H_e = (다음 화요일 + 3일) - 오늘.
    수량: Q = max(0, D*H_e*(1+b) - P) -> MOQ 올림. (수량은 D 기준, 트리거는 D_alert)
    반환: 후보 아니면 None, 후보면 dict.
    """
    nt = next_tuesday(today)
    H_e = (nt + timedelta(days=EMERGENCY_ARRIVAL_DAYS)) - today
    H_e = H_e.days
    if H_e <= 0:
        return None
    threshold = _f(D_alert) * H_e
    if _f(stock) >= threshold:
        return None
    q = max(0.0, _f(D) * H_e * (1.0 + _f(buffer)) - max(0.0, _f(P)))
    return {
        "candidate": True,
        "H_e": H_e,
        "threshold": threshold,
        "qty": moq_ceil(q, moq),
        "raw_qty": q,
        "next_tuesday": nt,
    }


# ── 발주 달력 유틸 ────────────────────────────────────
def _is_tuesday(d):
    return d.weekday() == 1  # Monday=0


def tuesday_of_week(d):
    """d가 속한 주(월~일)의 화요일. 월~일 주차 기준이라 수~일에도 그 주 화요일을 반환."""
    monday = d - timedelta(days=d.weekday())
    return monday + timedelta(days=1)


def next_tuesday(d):
    """d 이후(당일 포함하지 않음) 가장 가까운 화요일. d가 화요일이면 다음 주 화요일."""
    delta = (1 - d.weekday()) % 7
    if delta == 0:
        delta = 7
    return d + timedelta(days=delta)


def is_regular_tuesday(d, anchor=CYCLE_ANCHOR):
    """정기주 화요일 판정: anchor(2026-11-10)로부터 14일 간격 화요일."""
    if not _is_tuesday(d):
        return False
    diff = (d - anchor).days
    return diff >= 0 and diff % REGULAR_PERIOD_DAYS == 0


def cycle_kind(d, anchor=CYCLE_ANCHOR):
    """'regular' | 'supply'(중간주) | 'not_tuesday' | 'before_anchor'."""
    if not _is_tuesday(d):
        return "not_tuesday"
    if d < anchor:
        return "before_anchor"
    return "regular" if is_regular_tuesday(d, anchor) else "supply"


def last_tuesday_of_month(year, month):
    """해당 월의 마지막 화요일."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while not _is_tuesday(d):
        d -= timedelta(days=1)
    return d


def monthly_order_dates(start_year=2026, start_month=1, end_year=2028, end_month=12):
    """기간 내 월 발주일(매월 마지막 화요일) 목록."""
    out = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        out.append(last_tuesday_of_month(y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def monthly_cycle_R(order_date):
    """월 발주일 기준 다음 월 발주일까지 일수 R (28 또는 35)."""
    d = order_date
    if d.month == 12:
        nxt = last_tuesday_of_month(d.year + 1, 1)
    else:
        nxt = last_tuesday_of_month(d.year, d.month + 1)
    return (nxt - d).days, nxt


def monthly_order_qty(D, P, order_date, buffer=DEFAULT_BUFFER, moq=1):
    """월 발주 수량: R=(다음 월 발주일-이번 월 발주일), L_max=11.
    Q = max(0, D*R*(1+b) - max(0, P - D*L_max)) -> MOQ 올림."""
    R, _nxt = monthly_cycle_R(order_date)
    return regular_order_qty(D, P, R=R, L_max=MONTHLY_LEAD_MAX_DAYS,
                             buffer=buffer, moq=moq)


def arrival_max_date(order_date, monthly=False):
    """발주일 -> 입고 최대일. 주간 +3일, 월간 +11일."""
    return order_date + timedelta(days=MONTHLY_LEAD_MAX_DAYS if monthly else REGULAR_LEAD_MAX_DAYS)


def apply_holiday_shift(order_date, holidays=None):
    """공휴일 이동 인터페이스. holidays(설정 가능 iterable of date)를 넘기면
    order_date가 공휴일/주말이면 직전 영업일로 이동. 기본(None)은 미적용.
    이번 단계에서는 기본 미적용 — 호출부에서 명시적으로 holidays를 넘길 때만 동작."""
    if not holidays:
        return order_date
    hol = set(holidays)
    d = order_date
    while d.weekday() >= 5 or d in hol:
        d -= timedelta(days=1)
    return d


def business_days_between(d0, d1):
    """d0(배타) ~ d1(포함) 사이 영업일 수(토·일 제외, 공휴일 미반영)."""
    if d1 <= d0:
        return 0
    n = 0
    d = d0 + timedelta(days=1)
    while d <= d1:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n
