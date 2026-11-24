# R3 — 발주 방식 선택(order_cycle) 설계 + 지방점 월 발주 현황
작성: Devin, 2026-11-24, 근거: _verify_out/snapshot_20261124.db

## 1. 현재 분류 체계 (코드 확정)
- `_compute_purchase_order_candidates` @main.py:18568-18714
- 분류 규칙: `lead_time_days >= 35` OR `purchase_order_monthly_exception(item_code|item_name, branch_code)` → monthly, 나머지 weekly.
- **달력 없음**: 매 실행마다 weekly/monthly 리스트를 둘 다 산출. 마스터가 각각 엑셀 다운로드.
- **is_consumable 게이트 @18664**: is_consumable=TRUE 품목만 후보. 월간 예외 45행(전부 is_consumable=0)은 분류 단계 전에 탈락 → **현재 monthly 발주서의 실질 내용은 lead=35 소모품 1품목([숨의] 보라실_100개)뿐**.

## 2. 스냅샷 재현 결과 (2026-11-24 기준)
safety_stock qty>0 = 837행(비본사)
- 제외: 재고충분 600 / 비소모품 146 / 전체미발주 59 / 미입고보류 4
- **weekly 후보 21건** (전부 CM* 소모품: 리패키징쇼핑백, 발도장 엽서, 종이액자, 서비스케어 안내서/홀더, 스톤제작보증서, 보라실 일부)
- **monthly 후보 7건** (전부 [숨의] 보라실_100개, lead=35)
- pending(접수/승인 최신 상태): 4건

## 3. 지방점 월 발주 현황 (purchase_order_monthly_exception, 45행 = 15품목 × 3지점)
| 지점 | 품목 수 | 품목 |
|---|---|---|
| 김해점 | 15 | [블루어] 스톤함 2 + 유골함 5 + 오동나무관 7 + 요람장세트 2 (전부 is_consumable=0, lead=14) |
| 부산점 | 15 | 동일 15품목 |
| 세종점 | 15 | 동일 15품목 |

- "지방점" = 김해점·부산점·세종점(경기권 외 3지점). 월 발주 예외가 정확히 이 조합.
- **주의**: 이 45행은 현재 발주서 판정에 **실효 없음**(비소모품이라 is_consumable 게이트에서 탈락). 지방점 월 발주가 실제로 이뤄지고 있다면 시스템 밖 수기 프로세스이며, v2에서 발주 범위를 비소모품까지 넓힐 때 이 테이블이 처음으로 실효를 가짐.
- lead_time_days=35 품목 35종 중 발주 후보 도달 가능(is_consumable=1)은 [숨의] 보라실_100개 1종뿐. 나머지 34종(수의/수의보/인견수의/기억머문담 명패/현진산업 관)은 비소모품 또는 order_excluded.

## 4. order_cycle 컬럼 설계안 (신규, 미구현)
```sql
ALTER TABLE product_master ADD COLUMN order_cycle TEXT;  -- 'weekly'|'biweekly'|'monthly'|'none'
```
- 값 의미: weekly=격주 정기 대상(현 "주간"의 재정의), monthly=월 발주, none=미발주.
- **기존 order_excluded와의 관계**: order_cycle='none'이 order_excluded를 대체하도록 단계적 이관. 1단계: order_cycle 추가+기존 값 백필(order_excluded→none, lead>=35→monthly, 나머지→weekly). 2단계: 판정 코드가 order_cycle 우선, NULL이면 기존 규칙 fallback. 3단계: order_excluded UI 제거.
- **purchase_order_monthly_exception 이관**: (item_code, branch_code) 조합이 지점별 예외이므로 order_cycle을 품번 단위로는 표현 못 함 → 지점별 오버라이드가 필요하면 `product_branch_order_exclusion`(현재 0행, 스키마 존재)과 같은 패턴의 `product_branch_order_cycle` 테이블 신설 또는 monthly_exception 유지 + order_cycle='monthly' 품번에만 적용. **권장**: 월 발주 = 품번 단위(order_cycle='monthly') + 지점 예외 테이블 유지(현행 45행 그대로 유효).
- **is_consumable 게이트와의 충돌(최대 리스크)**: v2 발주 범위가 "장례용품 전체+소모품 일부"라면 is_consumable 조건을 order_cycle로 대체해야 함. 대체 시 발주 후보가 21+7건 → 수백 건 규모로 증가(비소모품 146건이 게이트 통과). 안전재고 qty가 수동 설정값이라 대량 후보 발생 가능 — 파일럿 필수.
- 같은 품번 지점 간 동기: product_master는 item_code 단위 공통값이지만 행은 지점별 존재(197행/193코드). 저장 로직 @19922-19993은 item_code별 UPSERT — 지점별 다른 order_cycle은 품번 단위로만 가능(지점 예외는 별도 테이블).
