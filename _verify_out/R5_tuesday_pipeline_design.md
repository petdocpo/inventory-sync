# R5 — 화요일 파이프라인 설계서 (안전재고 자동 갱신 → 발주서 생성 → 알림)
작성: Devin, 2026-11-24. 코드 작성 없음, 설계만.

## 0. 전제 확인 (실측)
- 안전재고 갱신은 현재 **완전 수동**: `/master/purchase-order/safety-stock/compare` → 선택 적용(`safety_stock_compare_apply` @main.py:29025, batch_id + safety_stock_change_log 기록 + 하향 max_down_pct 제한 내장).
- 발주서는 **수동 다운로드**: `/master/purchase-order/preview/export?order_type=weekly|monthly` → `_build_purchase_order_excel` @main.py:27810. 자동 생성·발송 크론 없음.
- 크론 실행체: Vercel 엔드포인트 + cron-job.org 외부 스케줄. 작업 메타 `CRON_MANAGE_JOBS` @main.py:29916, on/off `cron_enabled:{job}` in system_settings, 실패 `report_cron_failure` → cron_failure_log.
- `sync-sales-records`: 매일 전날분, 07시 슬롯(실측 created_at 07:00). `sync-raw-inventory`: 15분 주기 auto_s3.
- 실제 발주는 외부 Supabase `purchase_history`(21일 보관) — 본 시스템 발주서는 "생성→다운로드→수동 발송" 구조.

## 1. 엔드포인트 구성 (제안)
```
GET /api/cron/tuesday-order-pipeline?authorization=Bearer $CRON_SECRET
```
- 매일 호출 허용, 내부에서 "오늘(KST)이 실행일인가" 판정:
  - 화요일이고 order_cycle_mode != 'legacy' 이면 실행
  - 발주일 판정: 정기주 앵커(기본 11/10 기준 격주) + 월 발주일(매월 마지막 화요일) + 공휴일 보정(직전 영업일)을 순수 함수로 계산
  - 중간 화요일 = 정기주 아닌 화요일 → 보충 모드
- 비화요일 호출은 {"skipped": "not_tuesday"} 반환(멱등, cron-job.org 매일 호출로 단순화).

### 내부 단계 (각 단계 실패 시 후속 중단 + report_cron_failure)
1. **선행 확인**: sync-sales-records 최근 성공 여부(cron_failure_log/실행 로그), raw_inventory MAX(uploaded_at) 신선도(>60분이면 경고 후 계속/중단 정책).
2. **D 재계산**: `_compute_safety_recommendations` 로직을 기간 파라미터화해 호출(기본 100일, 추후 파라미터). 출력: 지점×품번별 D, S=ceil(D×(R+L)×(1+b)), F(중간주).
3. **안전재고 갱신**: safety_auto_update_enabled='true'이고 safety_stock_auto_exclude(row 단위 "자동 갱신 제외" 체크) 아닌 행에 대해 qty/safety_qty 갱신. **safety_stock_change_log에 batch_id로 기록**(period, factor, min_used, note='auto_tuesday').
   - 변동 가드: 기존 max_down_pct(하향 30%) 재사용 + **상향 상한 신규 제안**(예: 전주 대비 +50% 또는 절대 상한 — 과거 change_log 분포로 결정). 가드 초과분은 자동 반영 제외 + 마스터 알림.
4. **발주 판정**: order_cycle_mode='v2'이면 신규 판정 함수(정기=4-A식 S−P, 중간=F−P 상한 S−P, 월간=R 가변), 아니면 기존 `_compute_purchase_order_candidates`.
5. **발주서 동결 저장**: `order_run` + `order_run_line` 테이블(아래)에 run_id 기준으로 라인 저장 — 이 시점의 D,S,F,P,재고 스냅샷 시각, final_qty를 **값으로 동결**. 이후 safety_stock가 바뀌어도 이 run의 발주서는 불변.
6. **알림**: 마스터 Teams 채널로 "발주서 생성 완료 N건, 다운로드 링크" + 변동 가드 초과/실패 요약.

## 2. 신규 테이블 (최소 설계)
```sql
order_run (
  run_id TEXT PRIMARY KEY,            -- YYYYMMDD-HHMMSS-xxxx
  run_type TEXT,                     -- regular|supply|monthly|legacy_preview
  order_date DATE, cycle_anchor TEXT,
  mode TEXT,                         -- v2 inputs snapshot
  sales_through DATE,                -- 계산에 사용한 판매 마지막일
  raw_snapshot_at TIMESTAMPTZ,       -- raw_inventory MAX(uploaded_at)
  created_at TIMESTAMPTZ
)
order_run_line (
  run_id TEXT, branch_code TEXT, branch_name TEXT,
  item_code TEXT, item_name TEXT,
  D REAL, S REAL, F REAL, P REAL, current_qty INT,
  moq INT, final_qty INT,
  UNIQUE(run_id, branch_code, item_code)
)
safety_stock_auto_exclude (          -- 수동 고정 품목
  branch_name TEXT, item_code TEXT, created_at, PRIMARY KEY(branch_name,item_code)
)
```
- 멱등: 같은 KST 날짜 재실행 시 `order_run(run_type+order_date)` UNIQUE → 기존 run 재사용 또는 'duplicate_skipped' 반환. 새 run_id를 만들되 UNIQUE 제약으로 중복 라인 생성 차단.

## 3. safety_stock.qty에 저장할 값 — 결정안
- **권장: qty = S(정기 목표 보유량)**. 이유: qty는 "목표 재고"로 해석되고, 발주 트리거(current<qty)와 수동 화면이 이 의미로 사용 중. F(중간주 보충선)는 실행 기록(order_run_line)에만 저장하고 safety_stock에는 쓰지 않음 — 주간마다 F가 바뀌어 safety_stock를 오염시키지 않기 위함.
- 레거시 복귀: order_cycle_mode='legacy' → 파이프라인은 safety 갱신만 수행(또는 전면 skip)하고 발주서는 기존 preview/export 수동 경로 사용. safety_stock 값은 v2 S 기준이므로 legacy 복귀 시 **compare 화면에서 재계산·재적용 필요** — 배치ID 단위 롤백(safety_stock_change_log.reverted_at 활용, 이미 컬럼 존재)을 복귀 절차에 포함 권장.

## 4. cron-job.org 스케줄 제안
- `/api/cron/tuesday-order-pipeline`: **매일 09:30 KST** 호출(내부에서 화요일만 실행). sync-sales-records(07시) 완료 후, 영업시간 전.
- 충돌 점검: sync-raw-inventory 15분 주기는 UPSERT라 읽기와 경합 없음. 발주 계산 도중 재고가 갱신돼도 스냅샷 시각을 기록하므로 감사 가능(완전 격리 원하면 계산 시작 시 raw_inventory를 메모리로 1회 읽어 일관 스냅샷 사용 — fetch_raw_inventory가 이미 전체 SELECT라 자연 충족).
- 안전재고 갱신과 발주 생성을 **한 엔드포인트**로 묶되, safety_auto_update_enabled=false면 3단계 skip하고 4단계는 기존 qty로 진행 — 두 플래그 독립.

## 5. 발주서 다운로드 구조
- v2: order_run 생성 후 마스터가 `/master/purchase-order/run/{run_id}/export`로 **동결된 라인**에서 엑셀 생성(기존 `_build_purchase_order_excel` 재사용, 입력만 order_run_line으로 교체). "생성 시점 값"이므로 재고 변동과 무관하게 동일 파일.
- 기존 preview/verify 화면과의 관계: preview는 계속 "지금 계산" 미리보기로 유지(수동 확인용), verify는 order_run_line vs 기존 양식 비교로 확장 가능. 다운로드 이력 = order_run 목록 페이지.
- 발송은 여전히 수동(한서 요구와 일치: "한서가 다운로드·발송").

## 6. 위험·미검증
- **is_consumable 게이트 미해결**: 발주 범위(소모품만 vs 장례용품 포함)가 확정되기 전 자동화하면 후보 규모가 28건 → 수백 건으로 달라짐. R1 결론(코드상 소모품만, 한서 진술은 실제 구매 범위)과 한서 확인 전까지 order_cycle_mode='v2'는 파일럿 제한 권장.
- 소모품 수요 신호 부재: 소모품은 판매(sales_records)에 거의 안 잡힘(35개 코드 중 판매 있는 것 4개, 총 141개). D 기반 S가 소모품에는 min값 수준에 머묾 → 소모품 발주량 산출 근거를 별도 확인 필요(사용량 데이터 없음).
- 공휴일 판정: 한국 공휴일 데이터 출처 필요(고정 목록 vs API). 화요일 공휴일 빈도 낮으나 연말연시·설·추석 대응 규칙 필요.
- pending 상태 9월부터 지속 948건 — P 계산 시 "미입고"로 계속 잡히면 중복 발주 억제가 과도해질 수 있음. ETA 없으므로 "최대일 경과+미입고=지연" 표시만으로는 부족, 일정 기간 초과 시 알림으로 사람 판단 유도 권장.
