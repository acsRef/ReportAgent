-- ═══════════════════════════════════════════════════════════════
-- ReportAgent 业务测试数据（现役 canonical 星型 schema）
-- 零售订单场景：fact_orders + fact_payments + dim_*（store/product/customer/date/promotion）
-- 为 sql_repair 验证故意设计「易拼错列名」（order_amount / quantity / payment_method）
-- 数据量：5000 orders + 5000 payments + 30 stores + 50 products + 100 customers + 365 dates + 10 promotions
--
-- 历史：本文件 2026-09-02 P15 prelude 引入（当时只重建 7 张新表）；2026-09-03
-- Final Hardening 起承担 canonical 角色——同时清理 2026-08-04 旧 10 表演示 schema
-- （fact_sales/fact_returns/fact_inventory/fact_attendance + dim_region/
-- dim_warehouse/dim_employee），避免新旧业务表并存导致 LLM 命中陈旧数据表。
-- ═══════════════════════════════════════════════════════════════

-- 旧演示 schema（2026-08-04 seed_pg.sql 产物）退役清理
DROP TABLE IF EXISTS fact_attendance CASCADE;
DROP TABLE IF EXISTS fact_inventory CASCADE;
DROP TABLE IF EXISTS fact_returns CASCADE;
DROP TABLE IF EXISTS fact_sales CASCADE;
DROP TABLE IF EXISTS dim_employee CASCADE;
DROP TABLE IF EXISTS dim_warehouse CASCADE;
DROP TABLE IF EXISTS dim_region CASCADE;

-- 现役 schema 重建
DROP TABLE IF EXISTS fact_payments CASCADE;
DROP TABLE IF EXISTS fact_orders CASCADE;
DROP TABLE IF EXISTS dim_promotion CASCADE;
DROP TABLE IF EXISTS dim_customer CASCADE;
DROP TABLE IF EXISTS dim_product CASCADE;
DROP TABLE IF EXISTS dim_store CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

-- ── 1. 日期维度（2024 整年 365 天） ──────────────────────────────
CREATE TABLE dim_date (
    date_id INTEGER PRIMARY KEY,
    full_date DATE NOT NULL,
    year INTEGER NOT NULL,
    quarter_num INTEGER NOT NULL,
    quarter VARCHAR(10) NOT NULL,
    month INTEGER NOT NULL,
    week_of_year INTEGER NOT NULL,
    day_of_week VARCHAR(10) NOT NULL,
    is_holiday INTEGER NOT NULL
);

INSERT INTO dim_date
SELECT
    TO_CHAR(d, 'YYYYMMDD')::int AS date_id,
    d AS full_date,
    EXTRACT(YEAR FROM d)::int AS year,
    EXTRACT(QUARTER FROM d)::int AS quarter_num,
    'Q' || EXTRACT(QUARTER FROM d)::int AS quarter,
    EXTRACT(MONTH FROM d)::int AS month,
    EXTRACT(WEEK FROM d)::int AS week_of_year,
    TRIM(TO_CHAR(d, 'day')) AS day_of_week,
    CASE WHEN EXTRACT(DOW FROM d) IN (0, 6) THEN 1 ELSE 0 END AS is_holiday
FROM generate_series('2024-01-01'::date, '2024-12-31'::date, '1 day') d;

-- ── 2. 门店维度（30 家：6 区域 × 5 类型） ──────────────────────────────
CREATE TABLE dim_store (
    store_id INTEGER PRIMARY KEY,
    store_name VARCHAR(64) NOT NULL,
    region VARCHAR(16) NOT NULL,
    city VARCHAR(16) NOT NULL,
    store_type VARCHAR(16) NOT NULL,
    open_date DATE NOT NULL
);

INSERT INTO dim_store
SELECT
    n,
    '门店' || n,
    (ARRAY['华北','华东','华南','华中','西南','西北'])[1 + (n % 6)],
    (ARRAY['北京','上海','广州','武汉','成都','西安'])[1 + (n % 6)],
    (ARRAY['旗舰店','标准店','加盟店','快闪店'])[1 + (n % 4)],
    '2020-01-01'::date + ((n * 30) || ' days')::interval
FROM generate_series(1, 30) n;

-- ── 3. 产品维度（50 个：6 类目 × 5 品牌） ──────────────────────────────
CREATE TABLE dim_product (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    brand VARCHAR(32) NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);

INSERT INTO dim_product
SELECT
    n,
    '商品' || n,
    (ARRAY['手机','平板','耳机','音箱','智能手表','配件'])[1 + (n % 6)],
    (ARRAY['品牌A','品牌B','品牌C','品牌D','品牌E'])[1 + (n % 5)],
    (100 + (random() * 9000))::numeric(10, 2)
FROM generate_series(1, 50) n;

-- ── 4. 客户维度（100 个：3 类型 × 4 VIP 等级） ──────────────────────────────
CREATE TABLE dim_customer (
    customer_id INTEGER PRIMARY KEY,
    customer_name VARCHAR(64) NOT NULL,
    customer_type VARCHAR(16) NOT NULL,
    region VARCHAR(16) NOT NULL,
    vip_level VARCHAR(16) NOT NULL
);

INSERT INTO dim_customer
SELECT
    n,
    '客户' || n,
    (ARRAY['个人','企业','政府'])[1 + (n % 3)],
    (ARRAY['华北','华东','华南','华中','西南','西北'])[1 + (n % 6)],
    (ARRAY['普通','银卡','金卡','钻石'])[1 + (n % 4)]
FROM generate_series(1, 100) n;

-- ── 5. 促销维度（10 个） ──────────────────────────────
CREATE TABLE dim_promotion (
    promotion_id INTEGER PRIMARY KEY,
    promo_name VARCHAR(64) NOT NULL,
    discount_rate NUMERIC(3, 2) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL
);

INSERT INTO dim_promotion
SELECT
    n,
    '促销' || n,
    (0.70 + (random() * 0.25))::numeric(3, 2),
    '2024-01-01'::date + ((n * 30) || ' days')::interval,
    '2024-01-01'::date + ((n * 30 + 14) || ' days')::interval
FROM generate_series(1, 10) n;

-- ── 6. 订单事实表（5000 行：易拼错列名 order_amount / quantity） ──────────────────────────────
CREATE TABLE fact_orders (
    order_id INTEGER PRIMARY KEY,
    order_date DATE NOT NULL,
    store_id INTEGER NOT NULL REFERENCES dim_store(store_id),
    customer_id INTEGER NOT NULL REFERENCES dim_customer(customer_id),
    product_id INTEGER NOT NULL REFERENCES dim_product(product_id),
    promotion_id INTEGER REFERENCES dim_promotion(promotion_id),
    quantity INTEGER NOT NULL,
    order_amount NUMERIC(10, 2) NOT NULL,
    payment_method VARCHAR(16) NOT NULL
);

CREATE INDEX idx_fact_orders_date ON fact_orders(order_date);
CREATE INDEX idx_fact_orders_store ON fact_orders(store_id);

INSERT INTO fact_orders
SELECT
    n,
    '2024-01-01'::date + ((random() * 365)::numeric || ' days')::interval,
    1 + (random() * 29)::int,
    1 + (random() * 99)::int,
    1 + (random() * 49)::int,
    CASE WHEN random() < 0.3 THEN 1 + (random() * 9)::int ELSE NULL END,
    1 + (random() * 5)::int,
    ((1 + (random() * 5)::int) * (100 + random() * 9000))::numeric(10, 2),
    (ARRAY['微信','支付宝','银行卡','现金','Apple Pay'])[1 + (random() * 4)::int]
FROM generate_series(1, 5000) n;

-- ── 7. 支付事实表（5000 行） ──────────────────────────────
CREATE TABLE fact_payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES fact_orders(order_id),
    payment_date DATE NOT NULL,
    payment_amount NUMERIC(10, 2) NOT NULL,
    payment_method VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL
);

CREATE INDEX idx_fact_payments_order ON fact_payments(order_id);

INSERT INTO fact_payments
SELECT
    n,
    n,
    '2024-01-01'::date + ((random() * 365)::numeric || ' days')::interval,
    ((1 + (random() * 5)::int) * (100 + random() * 9000))::numeric(10, 2),
    (ARRAY['微信','支付宝','银行卡','现金','Apple Pay'])[1 + (random() * 4)::int],
    (ARRAY['SUCCESS','REFUNDED','PENDING'])[1 + (random() * 2)::int]
FROM generate_series(1, 5000) n;