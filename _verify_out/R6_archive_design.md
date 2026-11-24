# R6 — 장기 데이터 보관(아카이브) 설계 + 한서 제공 파일 명세
작성: Devin, 2026-11-24

## 1. 현행 보유 기간 실측 (코드+DB)
| 데이터 | 테이블 | 보유 | 근거 |
|---|---|---|---|
| 판매내역 | sales_records | **100일** | SALES_RETENTION_DAYS=100 @main.py:22127, sync 시 삭제 @22350 |
| 제외 판매 | sales_excluded_summary | 100일 | 동일 cutoff @22362 |
| 구매내역 | purchase_records | cleanup 100일(created_at) + 업로드 시 3개월 이전 삭제(코드 주석 상이 — 두 경로 존재, 실효는 빠른 쪽) | @388, @26500 |
| 발주 트래킹 | purchase_tracking_snapshot | 180일 | @392 |
| 스캔/조정/알림 | scan_log, adjustment_log, notification_events | 100일 | @389-391 |
| 실제 발주 | purchase_history (외부 Supabase) | **21일** | delete_old_purchase_history(days=21) @273 |
| 현재고 | raw_inventory, inventory, safety_stock | **이력 없음**(최신값 덮어쓰기) | @21150 UPSERT |
| 안전재고 변경 | safety_stock_change_log | 삭제 정책 없음(전량 보존, 현재 70행) | — |

- 결측: 재고·안전재고의 **시계열이 없음** — 과거 특정일의 재고를 재구성 불가(판매로 역산만 가능, 입고 시점 불명). 백테스트 한계의 근본 원인.

## 2. 아카이브 설계안
### 2.1 판매 아카이브 (최소·권장)
- `sales_archive` 테이블 = sales_records와 동일 스키마 + `archived_at` 컬럼.
- `_sync_sales_records`의 보관기간 삭제 로직(@22350-22366)을 "DELETE"에서 "INSERT INTO sales_archive SELECT ... + DELETE"로 변경 — 삭제 대신 이동.
- 용량: 현재 100일 42,563행 → 연 ~15만 행, TEXT 중심 수 MB/년. Postgres 부담 없음.
- 대안(보조): 매월 1일 cron으로 전월분 CSV를 S3(`petdoc-ubiplus` 버킷)에 적재 — DB 부담 제로, 복원 시 재적재. **권장: DB 아카이브 우선, CSV는 이중화**.

### 2.2 재고 스냅샷 (신규, 선택)
- raw_inventory는 15분마다 최신값만 유지 → 하루 1회(예: 23:59 KST) `inventory_eod_snapshot(date, branch_code, item_code, quantity)` 적재 cron 추가. 백테스트의 초기 재고 가정 문제를 근본 해소.
- 용량: 1,740행/일 × 365 = ~63만 행/년 — 문제없음.
- purchase_records도 status 전이 시점(입고일)이 필요한데 purchase_datetime만 있으므로, `purchase_records`에 `received_at` 컬럼 추가 또는 입고 확인 시각 기록 권장(범위 밖이면 B 요청으로).

### 2.3 크론 연동
- 기존 `cleanup-old-data`(@379)와 sync-sales-records의 retention 블록(@22350)에 아카이브 단계를 끼워넣는 방식 — 신규 크론 추가 없이 기존 흐름 재사용.
- 롤백: 아카이브 INSERT 실패 시 기존 DELETE는 진행하되 경고(데이터 손실 방지 우선) — 단 아카이브 실패가 반복되면 알림.

## 3. 한서 제공 파일 명세
### 3.1 판매내역 (기간 확장용, 예: 2026-07-05~10-11)
- **형식**: .xlsx, 헤더는 위쪽 5줄 안에 `판매일시, 지점, 고객명, 상품명, 품번, 수량` 6개 컬럼(정확한 이름, `_parse_sales_xlsx` @22376 기준).
- 판매일시 형식: `YYYY-MM-DD HH:MM` 등 date_formats 목록 중 하나.
- 지점명은 시스템과 동일 표기(예: `경기 김포점`) — 불일치 시 별도 매핑표 필요.
- 품번 없는 행(서비스 항목)도 포함되어 있으면 OK — 로더가 자동 분리.
- **주의**: 기존 `/master/purchase-tracking/upload-sales`는 100일 cutoff 이전 행을 거부(@22486-22492). 과거 데이터 적재는 **아카이브 테이블 또는 검증용 별도 로딩**이 필요 — 운영 sales_records에 과거분을 넣으면 retention 삭제 로직이 즉시 지울 수 있으므로 검증 전용 경로로 분리 권장.

### 3.2 구매내역 (실발주 대조용)
- 기존 `_process_purchase_records_upload` @26499가 읽는 형식: 헤더 2행, `구매일시` 포함 행, 컬럼: 구매일시/지점/거래처/상품명/품번/수량/단가 등(엑셀 양식 그대로 제공 요청).
- 검증용 로더: `_verify_out/` 내 스크립트로 파싱만 수행(운영 적재 금지).

### 3.3 파일 도착 시 절차 (준비 완료)
1. `_verify_in/`에 파일 배치(한서가 넣으면 됨 — 현재 폴더 없음, 생성 예정).
2. `_verify_out/load_verify_files.py`(작성 예정)가 읽기 전용으로 파싱 → 행 수/기간/결측/기존 스냅샷과의 중복·차이 리포트 출력. 운영 DB 쓰기 없음.
3. 결과를 백테스트 기간 확장 판단에 사용.

## 4. 미검증·한계
- purchase_records 보유 기간 두 경로(100일 cron vs 3개월 업로드 삭제) 중 실효값 미확정 — cleanup-old-data가 실제로 purchase_records를 삭제하는지 cron_enabled 상태 확인 필요(현재 system_settings에 cleanup 관련 키 미확인).
- 아카이브 도입 전 과거 데이터는 복구 불가 — 7/5~10/11 엑셀이 유일한 확장 경로.
