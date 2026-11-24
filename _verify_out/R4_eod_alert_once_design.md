# R4 — 긴급 발주 알림 "최초 1회" 구조 설계 (코드 작성 금지)
작성: Devin, 2026-11-24

## 1. 현행 조사 결과
- 계산: `_eod_alert_candidates(lookback_days=56)` @main.py:15589-15676
  - 일평균 D = 최근 56일 판매량합/56, need = D × (이번 주 목요일까지 일수), stock < need 이면 후보.
  - 제외: pm 미등록 / is_consumable=TRUE / order_excluded / 판매 0 / eod_alert_exclusion(330행).
  - **품목 집합 = 비소모품(장례용품) 중심** — 발주서 후보(소모품)와 disjoint.
- 발송: `cron_send_eod_alert` @main.py:15777 — 매일 10시 cron-job.org 호출, `eod_alert_enabled='true'`일 때만.
  - **현재 enabled=false, test_enabled=true** (system_settings 실측 2026-11-16 갱신). teams_send_log에 실발송 0건, 테스트 발송만 존재(11/16, 36건).
- 중복 제어: **없음**. 같은 품목이 매일 조건 충족이면 매일 발송됨(현재 미가동이라 실측 불가). 알림 내용은 지점별 묶음 메시지.
- eod_alert_exclusion(330행): (branch_code, item_code, item_name) 수동 제외 목록 — 마스터가 특정 지점×품번의 알림을 끄는 용도.
- 수신 경로: `send_teams_notification` → teams_webhook(지점별 채널) + teams_send_log 기록(success 플래그 있음) + notification_events.

## 2. 요구사항 해석
"최초 1회만 보내고 DB에 기록" = 품절 위험 "에피소드" 단위 dedup. 에피소드 키 = (branch_code, item_code). 동일 에피소드가 지속되는 동안 재발송 금지, 해소 후 재발 시 신규 에피소드로 1회 발송.

## 3. 설계안

### 3.1 신규 테이블 `eod_alert_episode`
```sql
CREATE TABLE eod_alert_episode (
  id SERIAL PRIMARY KEY,
  branch_code TEXT NOT NULL,
  item_code   TEXT NOT NULL,
  item_name   TEXT,
  status      TEXT NOT NULL DEFAULT 'open',   -- open | resolved | suppressed
  first_alert_at TIMESTAMPTZ NOT NULL,
  last_seen_at   TIMESTAMPTZ NOT NULL,        -- 조건 충족 마지막 확인일
  resolved_at    TIMESTAMPTZ,
  resolve_reason TEXT,                        -- restocked | arrived | ordered | excluded | manual
  send_count     INT NOT NULL DEFAULT 1,
  last_send_ok   BOOLEAN,
  UNIQUE (branch_code, item_code, status)     -- PG는 partial unique 권장: WHERE status='open'
);
```
- open 에피소드가 있으면 재발송 안 함(UNIQUE로 동시 실행 경합 방지 — INSERT ... ON CONFLICT DO NOTHING).
- status='open' 부분 유니크 인덱스: `CREATE UNIQUE INDEX ... ON eod_alert_episode(branch_code,item_code) WHERE status='open'`.

### 3.2 상태 전이 (cron_send_eod_alert 내부)
1. `_eod_alert_candidates()` 실행 → 후보 집합 C.
2. open 에피소드 집합 O 조회.
3. **신규** = C − O → 각 건에 대해 open 에피소드 INSERT(실패=동시 실행 중복, 무시) + 발송 큐.
4. **지속** = C ∩ O → 발송 안 함, `last_seen_at`만 갱신(또는 갱신 생략 — 매일 UPDATE 비용 대비 weekly 갱신 권장).
5. **해소** = O − C → resolved 처리. 해소 조건(우선순위):
   - `restocked`: raw_inventory ≥ need (재고 회복)
   - `arrived`: 해당 품목에 status='입고'인 purchase_records가 에피소드 기간 내 존재
   - `ordered`: 에피소드 중 발주서/수동 발주 기록 존재(별도 order_run 연결 시)
   - `excluded`: eod_alert_exclusion에 추가됨
   - `manual`: 마스터 화면에서 수동 종료
6. **재발**: resolved 이후 다시 C에 등장 → 새 open 에피소드(UNIQUE는 status='open'만 대상이므로 허용), 다시 1회 발송.

### 3.3 발송 실패 재시도
- teams_send_log.success=false면 해당 에피소드는 "발송 예정" 상태로 유지하고 다음 cron에서 재시도. send_count는 성공 시에만 증가시키는 대신 `send_attempts` 컬럼 분리 권장.
- 발송 성공 판정: send_teams_notification 반환값(ok, detail) 활용 — 현재 반환 tuple 존재 @main.py:3811.

### 3.4 마스터 화면
- `/master/eod-alert/open`: open 에피소드 목록(지점, 품목, 최초 알림일, 경과일, 현재고/예상소진, 상태) + 수동 resolved/excluded 처리 버튼.
- 기존 eod_alert_exclusion 관리 UI가 있으면(@20290 부근 유사 패턴) 동일 패턴 재사용.

### 3.5 v2 연동 시 변경점
- v2 긴급 기준(H_e = 다음 화요일+3일−오늘, D_alert=max(장기,14일))으로 `_eod_alert_candidates` 내부 수식만 교체 — 에피소드/dedup 계층은 그대로 재사용 가능.
- 화요일에는 정기/중간 발주가 같은 품목을 커버하므로, 화요일 실행 시 "발주서에 포함된 품목"은 알림 에피소드를 resolved(reason='ordered') 처리하는 연결고리 필요 — order_run 기록에 (branch,item) 목록이 있으면 조인 가능.

## 4. 위험·미검증
- 현재 eod_alert_enabled=false — 실발송 데이터 없어 dedup 실효는 가동 후에야 검증 가능. 테스트 모드로 먼저 N주 운영 권장.
- 에피소드 해소 판정이 candidates 재계산에 의존 — sales_records 100일 보관으로 lookback 56일은 안전하나, 창 경계에서 품목이 빠지면 "해소"로 오판할 수 있음(판매 0 → 후보 제외 → resolved). 판매 없는 품목의 resolved는 사실상 맞지만, 일시적 데이터 결측 시 오종료 위험 → `last_seen_at`이 N일 이상 stale이면 자동 resolve 대신 'stale' 상태로 분리 권장.
- pending(접수/승인) 상태가 9월부터 지속하는 행 948건 — "arrived" 해소 판정 시 오래된 승인 건이 입고로 안 바뀌면 에피소드가 장기 open으로 남을 수 있음. resolve_reason='ordered'를 별도 발주 기록 기준으로 두는 이유.
