# 재고 마감 기능 운영 런북

> 대상: `/stock-closing`(재고 마감), `/stock-closing/status`(재고 마감 현황)
> 이 문서는 main.py 현재 코드 기준으로 작성했습니다. 확인하지 못한 항목은 "미확인"으로 표시합니다.
> ⚠️ 모든 SQL 예시는 **운영 DB에서 조건을 확인하고 실행**하세요. 삭제·갱신 전 대상 행을 SELECT로 먼저 확인하세요.

## 1. 기능 개요와 화면

| 화면 | 경로 | 권한 | 설명 |
|---|---|---|---|
| 재고 마감 | `/stock-closing` | 지점 계정(저장/마감), 마스터(조회만) | 날짜별 QR 입·출고 스캔 합계, QR 재고, 유비플러스 재고, 실사 수량 입력, 임시저장/마감 |
| 재고 마감 현황 | `/stock-closing/status` | 마스터/HQ만 | 지점별 제출 상태(미제출/임시저장/마감 완료), 자동 발송 설정, 지점별 알림 토글, 제출 요청/테스트 버튼 |

- 마감 가능 기간: **해당 일자 + 익일**(KST 기준, `_stock_closing_editable_dates`).
- 마감 가능 기간이 지나면 임시저장 값은 읽기 전용 스냅샷으로만 표시됩니다.
- 마감 완료 후에는 해당 날짜를 수정할 수 없습니다.
- "마감 담당자"는 마감 시 필수 입력(30자 제한, 지점 계정과 무관한 실제 작업자 이름).

## 2. 데이터

### 테이블

| 테이블 | 용도 |
|---|---|
| `stock_closing_saves` | 지점×날짜별 저장/마감 헤더 (status: draft/closed, closer_name, item_count, mismatch_count) |
| `stock_closing_lines` | 저장된 상품별 실사 수량·오차 (save_id로 연결) |
| `stock_daily_snapshot` | 슬롯(21/22/24시)별 지점×상품 QR/유비플러스 재고 스냅샷 — 마감 화면의 재고 기준값 |
| `system_settings` | 발송 스위치·지점 토글·크론 발송 기록 |
| `teams_webhook` | 지점별 Teams 웹훅 URL (테스트 채널 = branch_code `admin_hs`) |
| `teams_send_log` | 모든 발송 시도 기록 |
| `scan_log`, `inventory`, `raw_inventory` | 마감 화면의 스캔 합계·QR 재고·유비플러스 재고 원본 |

### system_settings 키 (재고 마감 관련, 모두 기본 꺼짐/없음)

| 키 | 의미 | 기본값 |
|---|---|---|
| `stock_closing_notify_branch` | 지점 채널 발송 스위치 ('on'만 켜짐) | off |
| `stock_closing_notify_test` | 테스트 채널(+자동 알림 시 테스트 메일) 스위치 | off |
| `stock_closing_notify_branch:<지점코드>` | 지점별 '알림' 토글 | off |
| `stock_closing_reminder_last` | 마지막 자동 발송 날짜(YYYY-MM-DD) — 중복 발송 방지 | 없음 |

## 3. 크론/배치 목록

| 경로 | 시각(KST) | 역할 | 인증 |
|---|---|---|---|
| `GET /api/cron/sync-payment-records?slot=21|22|24` | 슬롯별(21/22/24시대, 실제 스케줄은 외부 스케줄러 — **미확인**) | 결제내역 CSV 수신 + **재고 스냅샷 저장**(date 미지정 시에만) + 100일 보관 삭제 | `Authorization: Bearer $CRON_SECRET` |
| `GET /api/cron/stock-closing-reminder` | 매일 10:00(주석 기준, 실제 스케줄러 설정 **미확인**) | 전일 미제출 지점 자동 알림 | 동일 |
| `GET /api/cron/cleanup-old-data` | 미확인 | 100일 지난 scan_log 등 삭제 | 동일 |
| `GET /api/cron/cleanup-purchase-history` | 미확인 | 21일 지난 발주내역 삭제(Supabase) | 동일 |

- `CRON_SECRET` 환경변수가 **설정된 경우에만** Bearer 검사를 합니다. 미설정이면 인증 없이 호출 가능합니다(알려진 위험).
- 재고 스냅샷은 `sync-payment-records`가 `date` 파라미터 없이(자동 크론) 호출될 때만 실행됩니다. 수동 재수신(`?date=YYYYMMDD`)에서는 스냅샷이 돌지 않습니다(의도된 동작).

## 4. 스위치·토글 동작표

| 설정 | 켜면 생기는 일 |
|---|---|
| 지점 발송(상단) | 자동 알림·수동 '제출 요청'에서 **지점별 '알림' 체크가 켜진 지점**의 Teams 채널로 실제 발송 |
| 지점별 '알림' 체크(행) | 해당 지점 채널 발송 여부 (지점 발송 스위치와 AND) |
| 테스트 발송(상단) | 자동 알림·수동 '제출 요청'에 **테스트 채널 사본** 추가, 자동 알림은 **테스트 메일**도 발송 |
| 행 '제출 요청' 버튼 | 위 두 스위치·토글 조건에 따라 지점 채널/테스트 채널 발송. 둘 다 꺼져 있으면 아무것도 안 보냄 |
| 행 '테스트' 버튼 | 스위치 무관 **테스트 채널로만** 해당 지점 요청 1건 |
| '자동 알림 테스트' 버튼 | 스위치 무관 **테스트 채널+테스트 메일**로 미제출 요약 1건 |

- 자동 알림 크론은 매일 10시(KST)에 **전일** 날짜의 미제출·임시저장 지점을 대상으로 합니다.
- 스위치가 모두 꺼져 있으면 크론은 아무것도 하지 않고, `stock_closing_reminder_last`도 갱신하지 않습니다.

## 5. 운영 투입 권장 순서

1. 모든 스위치 OFF 상태 확인 → '테스트 발송'만 ON
2. '자동 알림 테스트' 버튼으로 테스트 채널·메일 수신 확인
3. 시범 지점 1곳의 '알림' 체크 ON → '지점 발송' ON → 수동 '제출 요청'으로 실발송 확인
4. 테스트 채널 수신물 확인 후 '테스트 발송' OFF(필요 시 유지), 나머지 지점 '알림' 순차 ON
5. 익일 10시 크론 자동 발송 결과를 `teams_send_log`/`payment`-류 로그가 아닌 `teams_send_log`와 화면 메시지로 확인

## 6. 장애·되돌리기

### 문법 오류로 배포가 깨진 경우 (2026-11 사고 유형)

- 커밋 전 반드시 `powershell -ExecutionPolicy Bypass -File check_before_commit.ps1` 실행 → PASS 확인 후 커밋/푸시.
- GitHub Actions `compile-check` 워크플로가 push/PR 시 `main.py` 컴파일을 검사합니다.
- 되돌리기: `git revert <문제 커밋>` 또는 `git reset --hard <정상 커밋>`( force push 필요 시 주의) → push → Vercel 재배포. Vercel은 빌드 실패 시 이전 정상 배포를 유지합니다(**실패 시 동작은 Vercel 정책상 일반적 추정 — 미확인**).

### 테스트/오발송 데이터 정리 (운영 DB, 조건 확인 후 실행)

```sql
-- 특정 지점·날짜의 마감 데이터 삭제 (lines 먼저)
DELETE FROM stock_closing_lines WHERE save_id IN (
  SELECT id FROM stock_closing_saves WHERE branch_code='지점코드' AND close_date='YYYY-MM-DD');
DELETE FROM stock_closing_saves WHERE branch_code='지점코드' AND close_date='YYYY-MM-DD';

-- 자동 알림 발송 기록 초기화(당일 재발송 허용하려면)
DELETE FROM system_settings WHERE key='stock_closing_reminder_last';

-- 특정 지점 알림 토글 끄기(전체 끄기는 화면 버튼 사용 권장)
UPDATE system_settings SET value='off' WHERE key='stock_closing_notify_branch:지점코드';
```

## 7. 알려진 위험과 제안 (미수정, 제안만)

| 위험 | 내용 | 제안 |
|---|---|---|
| 중복 발송 가능 | `stock_closing_reminder_last`가 발송 '완료 후' 기록 — 발송 중 예외/타임아웃 시 재실행하면 같은 날짜 재발송 | 발송 전 기록 또는 발송 단위 멱등키 도입 검토 |
| 당일 재시도 없음 | 부분 실패(일부 지점 발송 실패)여도 기록 갱신 → 다음날까지 실패 지점 재발송 안 됨 | 실패 지점만 별도 기록·재시도 검토 |
| CRON_SECRET 미설정 시 인증 생략 | env 미설정이면 크론 엔드포인트 무방비 | env 설정 확인, 미설정 시 fail-closed 권장 |
| 스냅샷 고아 행 | 100일 삭제 시 `stock_closing_saves`만 지워지고 `stock_closing_lines` 잔존 가능 | lines 삭제 추가 검토 |
| 슬롯 스냅샷 누락 | 지점별 '가장 늦은 슬롯'만 사용 — 해당 슬롯에 특정 상품 스냅샷이 없으면 live 값으로 표시 | 표시상 basis_label로 구분되므로 모니터링 권장 |
| 지점코드 정합성 | '테스트' 문자열 포함 지점은 발송 대상에서 제외되는데, teams_webhook에 깨진 branch_code 행이 있으면 테스트 채널(`admin_hs`) 지정 근거는 코드 주석상 '테스트용지점 행 없음' — **실제 webhook 등록 상태 미확인** | 운영 DB에서 `SELECT branch_code FROM teams_webhook` 확인 권장 |
| 제외 지점 스냅샷 | `남양주점`·`본사`·`테스트용지점`도 스냅샷 INSERT 대상(조회 시에만 제외) | 필요 시 스냅샷 생성 단계 제외 검토 |

## 8. 배포 전 점검 절차

1. `python -m unittest test_stock_closing -v` → 전체 PASS 확인(34건).
2. `powershell -ExecutionPolicy Bypass -File check_before_commit.ps1` → PASS.
3. `git diff --check` → 공백 오류 없음.
4. 브라우저 확인 체크리스트(배포 후):
   - [ ] `/stock-closing`: 9개 칼럼이 가로 스크롤 없이 보임, 'QR 수정' 버튼 잘리지 않음, 실사 입력칸이 칸 폭에 맞음
   - [ ] `/stock-closing/status`: 7개 칼럼 표시, '요청' 칸(알림/제출 요청/테스트) 넘침 없음
   - [ ] 칼럼 핸들 드래그 → 새로고침 후 너비 유지, 다른 표 영향 없음
   - [ ] 발송 설정 카드 문구가 실제 동작과 일치
   - [ ] 마감 담당자 입력 → 임시저장/마감 동작, 마감 후 수정 불가 표시
