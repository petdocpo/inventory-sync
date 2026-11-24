-- R2(c) lead_time_days → order_cycle 이관 마이그레이션 초안 (실행 금지, 검토용)
-- 전제: product_master.order_cycle TEXT 컬럼 신설 ('weekly'|'monthly'|'none')

-- 1) 컬럼 추가
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS order_cycle TEXT;

-- 2) 백필 1단계: 기존 분류 규칙 그대로 이관
--    order_excluded → 'none'
--    lead_time_days >= 35 → 'monthly'
--    purchase_order_monthly_exception 해당 품번 → 'monthly' (지점 예외는 별도 테이블 유지)
--    나머지 → 'weekly'
UPDATE product_master pm SET order_cycle = 'none'
WHERE order_excluded IS TRUE;

UPDATE product_master pm SET order_cycle = 'monthly'
WHERE order_cycle IS NULL AND lead_time_days >= 35;

UPDATE product_master pm SET order_cycle = 'monthly'
WHERE order_cycle IS NULL AND item_code IN (
  SELECT DISTINCT item_code FROM purchase_order_monthly_exception WHERE item_code IS NOT NULL AND item_code <> ''
);

UPDATE product_master pm SET order_cycle = 'weekly'
WHERE order_cycle IS NULL;

-- 3) 검증 쿼리 (실행 후 확인용)
-- SELECT order_cycle, COUNT(*) FROM product_master GROUP BY 1;
-- SELECT order_cycle, is_consumable, COUNT(*) FROM product_master GROUP BY 1,2;

-- 4) 주의사항
--  a) product_master는 item_code당 복수 행(지점별) 가능 — order_cycle은 품번 공통값 취급.
--     지점별 월 발주 예외는 purchase_order_monthly_exception(item_code, branch_code) 유지.
--  b) lead_time_days 컬럼은 유지: 안전재고 추천값(lead 차원)과 purchase_tracking_snapshot이 아직 사용.
--     UI 입력만 제거하려면 안전재고 공식을 "고정 14일"로 바꾸는 코드 변경이 선행되어야 함.
--  c) lead_time_days=0 인 54종은 안전재고 추천값이 최소값으로 고정됨(판매 있어도 rec=None→유지).
--     order_cycle과 무관하게 안전재고 계산에는 여전히 영향.
