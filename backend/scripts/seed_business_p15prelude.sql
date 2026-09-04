-- ═══════════════════════════════════════════════════════════════
-- ReportAgent 业务测试数据（realistic，2026-09-04 起）
-- 零售订单场景：fact_orders + fact_payments + dim_*（store/product/customer/date/promotion）
-- 真实模型：季节性 + 周末效应 + 促销绑定 + 支付一致 + 退款合理 + 头部店 + 品类价格梯度
-- 数据量：30000 orders + 30000 payments + 30 stores + 50 products + 100 customers + 365 dates + 10 promotions
--
-- 历史：本文件 2026-09-02 P15 prelude 引入（全 random 均匀分布 5k orders）；
-- 2026-09-04 起改为真实化模型（30k orders + 真实命名 + 季节性 + 周末 + 促销 + 支付一致 + 头部店）——
-- 详见 docs/plans/2026-09-04-realistic-business-data.md。
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

-- ── 2. 门店维度（30 家真实命名） ──────────────────────────────
CREATE TABLE dim_store (
    store_id INTEGER PRIMARY KEY,
    store_name VARCHAR(64) NOT NULL,
    region VARCHAR(16) NOT NULL,
    city VARCHAR(16) NOT NULL,
    store_type VARCHAR(16) NOT NULL,
    open_date DATE NOT NULL
);

WITH stores(store_id, name_suffix, region, city, store_type) AS (VALUES
    -- 华北（北京 5 家）
    (1,  '北京朝阳大悦城',         '华北', '北京', '旗舰店'),
    (2,  '北京西单大悦城',         '华北', '北京', '标准店'),
    (3,  '北京中关村鼎好',         '华北', '北京', '加盟店'),
    (4,  '北京三里屯通盈中心',     '华北', '北京', '旗舰店'),
    (5,  '北京望京凯德MALL',        '华北', '北京', '快闪店'),
    -- 华东（上海 5 家）
    (6,  '上海徐汇美罗城',         '华东', '上海', '旗舰店'),
    (7,  '上海浦东IFC国金',         '华东', '上海', '标准店'),
    (8,  '上海静安嘉里中心',        '华东', '上海', '加盟店'),
    (9,  '上海五角场万达广场',      '华东', '上海', '标准店'),
    (10, '上海虹口龙之梦',          '华东', '上海', '体验店'),
    -- 华南（广州 3 + 深圳 2）
    (11, '广州天河城',              '华南', '广州', '旗舰店'),
    (12, '广州正佳广场',            '华南', '广州', '标准店'),
    (13, '广州太古汇',              '华南', '广州', '加盟店'),
    (14, '深圳万象城',              '华南', '深圳', '旗舰店'),
    (15, '深圳海岸城',              '华南', '深圳', '快闪店'),
    -- 华中（武汉 4 + 长沙 1）
    (16, '武汉光谷步行街',          '华中', '武汉', '旗舰店'),
    (17, '武汉江汉路群光广场',      '华中', '武汉', '标准店'),
    (18, '武汉汉街万达',            '华中', '武汉', '加盟店'),
    (19, '武汉宜家荟聚中心',        '华中', '武汉', '体验店'),
    (20, '长沙IFS国金中心',         '华中', '长沙', '旗舰店'),
    -- 西南（成都 3 + 重庆 2）
    (21, '成都太古里',              '西南', '成都', '旗舰店'),
    (22, '成都IFS国际金融中心',     '西南', '成都', '标准店'),
    (23, '成都春熙路群光广场',      '西南', '成都', '加盟店'),
    (24, '重庆解放碑大都会',        '西南', '重庆', '旗舰店'),
    (25, '重庆观音桥北城天街',      '西南', '重庆', '标准店'),
    -- 西北（西安 3 + 西宁 1 + 兰州 1）
    (26, '西安赛格国际购物中心',    '西北', '西安', '旗舰店'),
    (27, '西安大唐不夜城',          '西北', '西安', '标准店'),
    (28, '西安曲江银泰城',          '西北', '西安', '加盟店'),
    (29, '西宁万达广场',            '西北', '西宁', '快闪店'),
    (30, '兰州中心',                '西北', '兰州', '体验店')
)
INSERT INTO dim_store (store_id, store_name, region, city, store_type, open_date)
SELECT store_id,
       name_suffix || store_type,
       region, city, store_type,
       '2018-01-01'::date + (store_id * 30 || ' days')::interval
FROM stores;

-- ── 3. 产品维度（50 个真实商品） ──────────────────────────────
CREATE TABLE dim_product (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    brand VARCHAR(32) NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);

WITH products(product_id, name, category, brand, unit_price) AS (VALUES
    -- 手机 8 个（均价 ~5500）
    (1,  '华为 Mate 60 Pro 12+512G',        '手机', '华为',     6999.00),
    (2,  '华为 P60 Pro 8+256G',             '手机', '华为',     4988.00),
    (3,  'Apple iPhone 15 Pro Max 256G',    '手机', 'Apple',    9999.00),
    (4,  'Apple iPhone 15 128G',            '手机', 'Apple',    5999.00),
    (5,  '小米 14 Ultra 16+512G',           '手机', '小米',     6499.00),
    (6,  '小米 14 Pro 12+256G',             '手机', '小米',     4999.00),
    (7,  'OPPO Find X7 Ultra 16+512G',      '手机', 'OPPO',     5999.00),
    (8,  'vivo X100 Pro 12+256G',           '手机', 'vivo',     4999.00),
    -- 平板 8 个（均价 ~4500）
    (9,  '华为 MatePad Pro 13.2 12+512G',   '平板', '华为',     5199.00),
    (10, 'Apple iPad Pro 12.9 256G',        '平板', 'Apple',    8999.00),
    (11, 'Apple iPad Air 11 128G',          '平板', 'Apple',    4799.00),
    (12, '小米 Pad 6 Pro 12+256G',          '平板', '小米',     3299.00),
    (13, '三星 Galaxy Tab S9 128G',         '平板', '三星',     5299.00),
    (14, '联想小新 Pad Pro 12.7 8+256G',    '平板', '联想',     2199.00),
    (15, '微软 Surface Pro 9 i5 8+256G',     '平板', '微软',     6988.00),
    (16, '华为 MatePad 11.5 8+128G',        '平板', '华为',     2299.00),
    -- 耳机 9 个（均价 ~1700）
    (17, 'Apple AirPods Pro 2 USB-C',       '耳机', 'Apple',    1899.00),
    (18, 'Apple AirPods 4',                 '耳机', 'Apple',    1299.00),
    (19, 'Sony WH-1000XM5 头戴',            '耳机', 'Sony',     2899.00),
    (20, 'Sony WF-1000XM5 入耳',            '耳机', 'Sony',     2399.00),
    (21, 'Bose QuietComfort Ultra',          '耳机', 'Bose',     3199.00),
    (22, 'Bose QC45 头戴',                  '耳机', 'Bose',     2299.00),
    (23, '华为 FreeBuds Pro 3',             '耳机', '华为',     1499.00),
    (24, '小米 Buds 4 Pro',                 '耳机', '小米',      999.00),
    (25, 'JBL Tour One M2',                 '耳机', 'JBL',      1999.00),
    -- 音箱 8 个（均价 ~1700）
    (26, 'Apple HomePod 2',                 '音箱', 'Apple',    2299.00),
    (27, 'Sonos Era 300',                   '音箱', 'Sonos',    4499.00),
    (28, 'Sonos One Gen 2',                 '音箱', 'Sonos',    1880.00),
    (29, 'JBL Charge 5 蓝牙',               '音箱', 'JBL',      1499.00),
    (30, 'Bose SoundLink Flex',             '音箱', 'Bose',     1399.00),
    (31, '华为 Sound Joy',                  '音箱', '华为',      999.00),
    (32, '小米 Sound Pro',                  '音箱', '小米',      699.00),
    (33, 'Marshall Stanmore III',           '音箱', 'Marshall', 3299.00),
    -- 智能手表 9 个（均价 ~3000）
    (34, 'Apple Watch Ultra 2',             '智能手表', 'Apple',    6499.00),
    (35, 'Apple Watch Series 9 45mm',       '智能手表', 'Apple',    3199.00),
    (36, '华为 Watch GT 4 46mm',            '智能手表', '华为',     1488.00),
    (37, '华为 Watch 4 Pro',                '智能手表', '华为',     2999.00),
    (38, '三星 Galaxy Watch 6 Classic',     '智能手表', '三星',     2899.00),
    (39, '小米 Watch S3',                   '智能手表', '小米',      999.00),
    (40, 'Garmin Fenix 7X',                 '智能手表', 'Garmin',   8480.00),
    (41, 'OPPO Watch 4 Pro',                '智能手表', 'OPPO',     2499.00),
    (42, 'Amazfit GTR 4',                   '智能手表', 'Amazfit',   999.00),
    -- 配件 8 个（均价 ~250）
    (43, 'Apple USB-C 数据线 1m',           '配件', 'Apple',      149.00),
    (44, 'Apple MagSafe 充电器',            '配件', 'Apple',      329.00),
    (45, '华为 66W 充电器',                 '配件', '华为',       199.00),
    (46, '小米 67W 充电器',                 '配件', '小米',       149.00),
    (47, 'Anker 65W 氮化镓',                '配件', 'Anker',      199.00),
    (48, '三星 T7 1TB 移动硬盘',            '配件', '三星',       799.00),
    (49, '罗技 MX Master 3S',               '配件', '罗技',       799.00),
    (50, '绿联 100W Type-C 线',             '配件', '绿联',        69.00)
)
INSERT INTO dim_product (product_id, product_name, category, brand, unit_price)
SELECT product_id, name, category, brand, unit_price FROM products;

-- ── 4. 客户维度（100 个真实姓名 + 区域 + VIP 等级） ──────────────────────────────
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
    (ARRAY['张','王','李','赵','刘','陈','杨','黄','周','吴'])[1 + (n % 10)] ||
    (ARRAY['先生','女士'])[1 + (n % 2)],
    (ARRAY['个人','企业','政府'])[1 + (n % 3)],
    (ARRAY['华北','华东','华南','华中','西南','西北'])[1 + (n % 6)],
    CASE
        WHEN n % 20 = 0 THEN '钻石'
        WHEN n % 5 = 0  THEN '金卡'
        WHEN n % 2 = 0  THEN '银卡'
        ELSE                 '普通'
    END
FROM generate_series(1, 100) n;

-- ── 5. 促销维度（10 个有真实名 + 折扣率 + 日期窗口） ──────────────────────────────
CREATE TABLE dim_promotion (
    promotion_id INTEGER PRIMARY KEY,
    promo_name VARCHAR(64) NOT NULL,
    discount_rate NUMERIC(3, 2) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL
);

INSERT INTO dim_promotion
VALUES
    (1,  '春节年货节',          0.85, '2024-01-20', '2024-02-10'),
    (2,  '女神节特惠',          0.80, '2024-03-01', '2024-03-08'),
    (3,  '春季新品发布',        0.90, '2024-03-15', '2024-04-05'),
    (4,  '五一劳动节大促',      0.75, '2024-04-25', '2024-05-05'),
    (5,  '618 年中庆',          0.70, '2024-06-01', '2024-06-18'),
    (6,  '暑期学生优惠',        0.85, '2024-07-01', '2024-08-31'),
    (7,  '99 大促',             0.78, '2024-09-01', '2024-09-09'),
    (8,  '国庆黄金周',          0.80, '2024-09-28', '2024-10-07'),
    (9,  '双11 全球狂欢节',     0.65, '2024-11-01', '2024-11-11'),
    (10, '圣诞+新年双节庆',     0.75, '2024-12-15', '2024-12-31');

-- ═══════════════════════════════════════════════════════════════
-- 6. 订单事实表（30000 行）—— 两阶段真实化
-- Stage A: 基础数据（按累计概率选月份 + 周末权重 + 品类加权）
-- Stage B: 头部店效应 + 促销绑定（后处理）
-- ═══════════════════════════════════════════════════════════════

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

-- ── Stage A: 30000 orders 基础插入 ──────────────────────────────
-- 月份按累计概率（季节性）：1月6% / 2月6% / 3月7% / 4月8% / 5月8% / 6月8%
--                          / 7月7% / 8月8% / 9月8% / 10月9% / 11月13% / 12月12% = 100%
-- 用 n % 100 做桶选择（确定性 300 个一组）
-- 品类按真实消费占比：手机25% / 平板12% / 耳机22% / 音箱10% / 智能手表16% / 配件15%
INSERT INTO fact_orders (order_id, order_date, store_id, customer_id, product_id, promotion_id, quantity, order_amount, payment_method)
SELECT
    n AS order_id,
    -- 月份按累计概率 + 月内随机日
    ('2024-01-01'::date +
        CASE
            WHEN (n % 100) < 6  THEN floor(random() * 30)::int           -- 1月 6%
            WHEN (n % 100) < 12 THEN 31 + floor(random() * 28)::int      -- 2月 6%
            WHEN (n % 100) < 19 THEN 60 + floor(random() * 30)::int      -- 3月 7%
            WHEN (n % 100) < 27 THEN 91 + floor(random() * 29)::int      -- 4月 8%
            WHEN (n % 100) < 35 THEN 121 + floor(random() * 30)::int     -- 5月 8%
            WHEN (n % 100) < 43 THEN 152 + floor(random() * 29)::int     -- 6月 8%
            WHEN (n % 100) < 50 THEN 182 + floor(random() * 30)::int     -- 7月 7%
            WHEN (n % 100) < 58 THEN 213 + floor(random() * 30)::int     -- 8月 8%
            WHEN (n % 100) < 66 THEN 244 + floor(random() * 29)::int     -- 9月 8%
            WHEN (n % 100) < 75 THEN 274 + floor(random() * 30)::int     -- 10月 9%
            WHEN (n % 100) < 88 THEN 305 + floor(random() * 29)::int     -- 11月 13%（双11）
            ELSE                       335 + floor(random() * 30)::int    -- 12月 12%
        END)::date AS order_date,
    -- store_id：30 家均匀分配（头部店效应放到 Stage B 单点重派，避免双重叠加）
    1 + floor(random() * 30)::int AS store_id,
    1 + floor(random() * 99)::int AS customer_id,
    -- 品类按真实消费占比（手机25% / 平板12% / 耳机22% / 音箱10% / 智能手表16% / 配件15%）
    CASE
        WHEN random() < 0.25 THEN 1 + floor(random() * 8)::int
        WHEN random() < 0.37 THEN 9 + floor(random() * 8)::int
        WHEN random() < 0.59 THEN 17 + floor(random() * 9)::int
        WHEN random() < 0.69 THEN 26 + floor(random() * 8)::int
        WHEN random() < 0.85 THEN 34 + floor(random() * 9)::int
        ELSE                       43 + floor(random() * 8)::int
    END::int AS product_id,
    NULL::int AS promotion_id,            -- Stage B 后处理
    1 + floor(random() * 4)::int AS quantity,  -- 1-5
    -- 金额先按品类均价估算（Stage B 促销绑定后再根据 discount 调整）
    ((
        (1 + floor(random() * 4)::int) *
        CASE
            WHEN random() < 0.25 THEN 1000 + random() * 9000     -- 手机
            WHEN random() < 0.37 THEN 2000 + random() * 7000     -- 平板
            WHEN random() < 0.59 THEN 500 + random() * 3000      -- 耳机
            WHEN random() < 0.69 THEN 500 + random() * 4000      -- 音箱
            WHEN random() < 0.85 THEN 1000 + random() * 7500     -- 智能手表
            ELSE                       50 + random() * 800        -- 配件
        END
    )::numeric(10, 2)) AS order_amount,
    (ARRAY['微信','支付宝','银行卡','现金','Apple Pay'])[1 + floor(random() * 4)::int] AS payment_method
FROM generate_series(1, 30000) n;

-- ── Stage B: 头部店效应（top 5 店 30% 重派，单一来源避免双重叠加） ──────────────────────────────
UPDATE fact_orders
SET store_id = 1 + floor(random() * 5)::int
WHERE random() < 0.30;

-- ── Stage B: 促销绑定（30% 订单，date 真在窗口内） ──────────────────────────────
UPDATE fact_orders o
SET promotion_id = p.promotion_id
FROM (
    SELECT promotion_id, start_date, end_date, random() AS r
    FROM dim_promotion, generate_series(1, 3)  -- 3 倍机会
) p
WHERE o.promotion_id IS NULL
  AND o.order_date BETWEEN p.start_date AND p.end_date
  AND random() < 0.30;  -- 30% 绑定

-- 促销订单数量 ×1.3（向上取整）
UPDATE fact_orders
SET quantity = CEIL(quantity * 1.3)
WHERE promotion_id IS NOT NULL;

-- 促销订单金额按 discount_rate 折扣（用 dim_promotion.discount_rate）
UPDATE fact_orders o
SET order_amount = (
    o.order_amount * (1 - p.discount_rate * 0.3)  -- 实际折扣 = rate × 30%（如 0.65 双11 → 19.5% off）
)::numeric(10, 2)
FROM dim_promotion p
WHERE o.promotion_id = p.promotion_id;

-- ── 7. 支付事实表（30000 行，payment_method/status/date 与 orders 一致） ──────────────────────────────
CREATE TABLE fact_payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES fact_orders(order_id),
    payment_date DATE NOT NULL,
    payment_amount NUMERIC(10, 2) NOT NULL,
    payment_method VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL
);

CREATE INDEX idx_fact_payments_order ON fact_payments(order_id);

-- 状态分布：SUCCESS 75% / REFUNDED 15% / PENDING 10%（用 n % 100 桶）
-- payment_date = order_date + 0-7 天
-- payment_method = orders.payment_method（必须一致）
-- REFUNDED 退 30%-70% 部分退款
INSERT INTO fact_payments (payment_id, order_id, payment_date, payment_amount, payment_method, status)
SELECT
    o.order_id AS payment_id,
    o.order_id,
    o.order_date + floor(random() * 7)::int AS payment_date,
    CASE
        WHEN (o.order_id % 100) < 75 THEN o.order_amount                                       -- SUCCESS 全额
        WHEN (o.order_id % 100) < 90 THEN (o.order_amount * (0.30 + random() * 0.40))::numeric(10, 2)  -- REFUNDED 退 30-70%
        ELSE                              o.order_amount                                       -- PENDING 全额
    END AS payment_amount,
    o.payment_method,                                                          -- 一致
    CASE
        WHEN (o.order_id % 100) < 75 THEN 'SUCCESS'
        WHEN (o.order_id % 100) < 90 THEN 'REFUNDED'
        ELSE                              'PENDING'
    END AS status
FROM fact_orders o;

-- ═══════════════════════════════════════════════════════════════
-- 8. ragent_readonly 权限恢复（重建表后必须重新 GRANT；setup_app_role.sql 是
-- 一次性脚本，重建后失效）。与 setup_app_role.sql §3 表清单保持一致。
-- ═══════════════════════════════════════════════════════════════
DO $$
DECLARE
    t text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragent_readonly') THEN
        FOREACH t IN ARRAY ARRAY[
            'dim_date', 'dim_store', 'dim_product', 'dim_customer', 'dim_promotion',
            'fact_orders', 'fact_payments'
        ]
        LOOP
            EXECUTE format('GRANT SELECT ON TABLE public.%I TO ragent_readonly', t);
        END LOOP;
    END IF;
END $$;
