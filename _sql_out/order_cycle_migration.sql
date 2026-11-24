-- ============================================================
-- product_master.order_cycle 백필 마이그레이션 (PostgreSQL / Supabase)
-- 작성일: 2026-11-24 / 상태: 실행 금지 — 검토 후 한서 승인 하에 실행
--
-- 배경: init_db()가 product_master.order_cycle TEXT 컬럼을 additive로
-- 추가한다(NULL 허용: 'weekly'|'monthly'|'none'). 코드는 NULL도
-- 레거시 규칙으로 해석하므로 이 SQL 없이도 동작한다.
-- 이 SQL은 "명시값 백필"이 필요할 때만 실행한다.
--
-- 백필 규칙 (effective_order_cycle의 레거시 분기와 동일):
--   order_excluded      -> 'none'
--   lead_time_days >=35 -> 'monthly'
--   그 외               -> 'weekly'
--   (지점별 월 발주 예외 purchase_order_monthly_exception은
--    품번 단위 order_cycle이 아니라 별도 테이블로 유지 — 백필하지 않음)
-- ============================================================

BEGIN;

-- 1) 백업 테이블 (롤백/검증용 스냅샷)
CREATE TABLE IF NOT EXISTS product_master_order_cycle_backup_20261124 AS
SELECT id, item_code, branch_name, lead_time_days, order_excluded, order_cycle, updated_at
FROM product_master;

-- 2) 컬럼 보강 (init_db가 이미 추가했어도 무해)
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS order_cycle TEXT;

-- 3) 백필 — order_cycle이 아직 NULL인 행만 (재실행 안전)
UPDATE product_master
SET order_cycle = 'none'
WHERE order_cycle IS NULL
  AND COALESCE(order_excluded, FALSE) IS TRUE;

UPDATE product_master
SET order_cycle = 'monthly'
WHERE order_cycle IS NULL
  AND COALESCE(lead_time_days, 0) >= 35;

UPDATE product_master
SET order_cycle = 'weekly'
WHERE order_cycle IS NULL;

-- 4) 확인 쿼리 (실행 후 수동 확인용)
-- SELECT order_cycle, COUNT(*) AS rows_cnt, COUNT(DISTINCT item_code) AS codes
-- FROM product_master GROUP BY order_cycle ORDER BY 1;
--
-- SELECT order_cycle, COALESCE(order_excluded::text,'NULL') AS excluded, COUNT(*)
-- FROM product_master GROUP BY 1,2 ORDER BY 1,2;
--
-- -- 백필 결과와 레거시 해석이 일치하는지 검증 (불일치 0이어야 함):
-- SELECT COUNT(*) AS mismatch
-- FROM product_master
-- WHERE order_cycle IS DISTINCT FROM CASE
--   WHEN COALESCE(order_excluded,FALSE) THEN 'none'
--   WHEN COALESCE(lead_time_days,0) >= 35 THEN 'monthly'
--   ELSE 'weekly' END;

COMMIT;

-- 롤백이 필요하면:
--   UPDATE product_master pm
--   SET order_cycle = b.order_cycle
--   FROM product_master_order_cycle_backup_20261124 b
--   WHERE pm.id = b.id;
-- (백업 테이블은 검증 완료 후 별도 지시로 삭제)
