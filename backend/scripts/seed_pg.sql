-- ═══════════════════════════════════════════════════════════════
-- ReportAgent 分析数据 — PostgreSQL 版
-- 10 张表，与 DuckDB seed_data.sql 结构一致
-- ═══════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS fact_attendance CASCADE;
DROP TABLE IF EXISTS fact_inventory CASCADE;
DROP TABLE IF EXISTS fact_returns CASCADE;
DROP TABLE IF EXISTS fact_sales CASCADE;
DROP TABLE IF EXISTS dim_employee CASCADE;
DROP TABLE IF EXISTS dim_warehouse CASCADE;
DROP TABLE IF EXISTS dim_customer CASCADE;
DROP TABLE IF EXISTS dim_product CASCADE;
DROP TABLE IF EXISTS dim_region CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

-- ── 1. 日期维度 ──────────────────────────────────────────────
CREATE TABLE dim_date (
    date_id INTEGER PRIMARY KEY,
    full_date DATE,
    year INTEGER,
    quarter_num INTEGER,
    quarter VARCHAR(10),
    week_of_year INTEGER,
    day_name VARCHAR(10),
    is_holiday INTEGER
);

INSERT INTO dim_date VALUES
(20240101, '2024-01-01', 2024, 1, 'Q1', 1, '周一', 0),
(20240102, '2024-01-02', 2024, 1, 'Q1', 1, '周二', 0),
(20240115, '2024-01-15', 2024, 1, 'Q1', 3, '周一', 0),
(20240116, '2024-01-16', 2024, 1, 'Q1', 3, '周二', 0),
(20240117, '2024-01-17', 2024, 1, 'Q1', 3, '周三', 0),
(20240201, '2024-02-01', 2024, 2, 'Q1', 5, '周四', 0),
(20240210, '2024-02-10', 2024, 2, 'Q1', 6, '周六', 1),
(20240211, '2024-02-11', 2024, 2, 'Q1', 6, '周日', 1),
(20240212, '2024-02-12', 2024, 2, 'Q1', 7, '周一', 1),
(20240220, '2024-02-20', 2024, 2, 'Q1', 8, '周二', 0),
(20240301, '2024-03-01', 2024, 3, 'Q1', 9, '周五', 0),
(20240305, '2024-03-05', 2024, 3, 'Q1', 10, '周二', 0),
(20240306, '2024-03-06', 2024, 3, 'Q1', 10, '周三', 0),
(20240307, '2024-03-07', 2024, 3, 'Q1', 10, '周四', 0),
(20240315, '2024-03-15', 2024, 3, 'Q1', 11, '周五', 0),
(20240320, '2024-03-20', 2024, 3, 'Q1', 12, '周三', 0),
(20240328, '2024-03-28', 2024, 3, 'Q1', 13, '周四', 0),
(20240401, '2024-04-01', 2024, 4, 'Q2', 14, '周一', 0),
(20240402, '2024-04-02', 2024, 4, 'Q2', 14, '周二', 0),
(20240403, '2024-04-03', 2024, 4, 'Q2', 14, '周三', 0),
(20240410, '2024-04-10', 2024, 4, 'Q2', 15, '周三', 0),
(20240501, '2024-05-01', 2024, 5, 'Q2', 18, '周三', 1),
(20240505, '2024-05-05', 2024, 5, 'Q2', 19, '周日', 0),
(20240510, '2024-05-10', 2024, 5, 'Q2', 19, '周五', 0),
(20240511, '2024-05-11', 2024, 5, 'Q2', 19, '周六', 0),
(20240512, '2024-05-12', 2024, 5, 'Q2', 20, '周日', 0),
(20240601, '2024-06-01', 2024, 6, 'Q2', 22, '周六', 0),
(20240602, '2024-06-02', 2024, 6, 'Q2', 22, '周日', 0),
(20240603, '2024-06-03', 2024, 6, 'Q2', 23, '周一', 0),
(20240615, '2024-06-15', 2024, 6, 'Q2', 24, '周六', 0),
(20240620, '2024-06-20', 2024, 6, 'Q2', 25, '周四', 0),
(20240701, '2024-07-01', 2024, 7, 'Q3', 27, '周一', 0),
(20240710, '2024-07-10', 2024, 7, 'Q3', 28, '周三', 0),
(20240711, '2024-07-11', 2024, 7, 'Q3', 28, '周四', 0),
(20240712, '2024-07-12', 2024, 7, 'Q3', 28, '周五', 0),
(20240715, '2024-07-15', 2024, 7, 'Q3', 29, '周一', 0),
(20240801, '2024-08-01', 2024, 8, 'Q3', 31, '周四', 0),
(20240802, '2024-08-02', 2024, 8, 'Q3', 31, '周五', 0),
(20240803, '2024-08-03', 2024, 8, 'Q3', 31, '周六', 0),
(20240810, '2024-08-10', 2024, 8, 'Q3', 32, '周六', 0),
(20240820, '2024-08-20', 2024, 8, 'Q3', 34, '周二', 0),
(20240825, '2024-08-25', 2024, 8, 'Q3', 34, '周日', 0),
(20240901, '2024-09-01', 2024, 9, 'Q3', 35, '周日', 0),
(20241001, '2024-10-01', 2024, 10, 'Q4', 40, '周二', 1),
(20241002, '2024-10-02', 2024, 10, 'Q4', 40, '周三', 1),
(20241101, '2024-11-01', 2024, 11, 'Q4', 44, '周五', 0),
(20241110, '2024-11-10', 2024, 11, 'Q4', 45, '周日', 1),
(20241201, '2024-12-01', 2024, 12, 'Q4', 48, '周日', 0),
(20241220, '2024-12-20', 2024, 12, 'Q4', 51, '周五', 0),
(20241225, '2024-12-25', 2024, 12, 'Q4', 52, '周三', 0),
(20241231, '2024-12-31', 2024, 12, 'Q4', 53, '周二', 0);

-- ── 2. 区域维度 ──────────────────────────────────────────────
CREATE TABLE dim_region (
    region_id INTEGER PRIMARY KEY,
    region_name VARCHAR(20),
    province VARCHAR(20),
    city VARCHAR(20),
    tier VARCHAR(10)
);

INSERT INTO dim_region VALUES
(1, '华北', '北京', '北京', '一线'),
(2, '华北', '天津', '天津', '一线'),
(3, '华北', '河北', '石家庄', '二线'),
(4, '华东', '上海', '上海', '一线'),
(5, '华东', '浙江', '杭州', '一线'),
(6, '华东', '江苏', '南京', '一线'),
(7, '华东', '江苏', '苏州', '二线'),
(8, '华南', '广东', '广州', '一线'),
(9, '华南', '广东', '深圳', '一线'),
(10, '华南', '广东', '东莞', '二线'),
(11, '西南', '四川', '成都', '一线'),
(12, '西南', '重庆', '重庆', '一线'),
(13, '西南', '云南', '昆明', '二线'),
(14, '西北', '陕西', '西安', '二线'),
(15, '西北', '甘肃', '兰州', '三线'),
(16, '东北', '辽宁', '沈阳', '二线'),
(17, '东北', '黑龙江', '哈尔滨', '二线');

-- ── 3. 产品维度 ──────────────────────────────────────────────
CREATE TABLE dim_product (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR(100),
    category VARCHAR(20),
    sub_category VARCHAR(20),
    brand VARCHAR(20),
    unit_price DECIMAL(10,2),
    cost_price DECIMAL(10,2),
    supplier VARCHAR(20)
);

INSERT INTO dim_product VALUES
(1, 'iPhone 15 Pro', '电子产品', '手机', 'Apple', 7999.00, 5500.00, '鸿海精密'),
(2, '华为 Mate 60 Pro', '电子产品', '手机', '华为', 6999.00, 4800.00, '比亚迪电子'),
(3, 'MacBook Air M3', '电子产品', '笔记本', 'Apple', 10999.00, 7500.00, '鸿海精密'),
(4, '联想 ThinkPad X1', '电子产品', '笔记本', '联想', 9999.00, 6800.00, '联宝电子'),
(5, 'Sony WH-1000XM5', '电子产品', '耳机', 'Sony', 2499.00, 1600.00, '歌尔股份'),
(6, 'AirPods Pro 2', '电子产品', '耳机', 'Apple', 1899.00, 1200.00, '立讯精密'),
(7, 'Nike Air Max', '服装鞋帽', '运动鞋', 'Nike', 1299.00, 600.00, '丰泰企业'),
(8, 'Adidas Ultraboost', '服装鞋帽', '运动鞋', 'Adidas', 1199.00, 550.00, '裕元工业'),
(9, '优衣库羽绒服', '服装鞋帽', '羽绒服', '优衣库', 799.00, 350.00, '申洲国际'),
(10, '海澜之家西装', '服装鞋帽', '西装', '海澜之家', 1599.00, 700.00, '海澜集团'),
(11, '茅台飞天53度', '食品饮料', '白酒', '茅台', 1499.00, 800.00, '贵州茅台'),
(12, '青岛啤酒经典', '食品饮料', '啤酒', '青岛啤酒', 8.00, 3.50, '青岛啤酒'),
(13, '农夫山泉矿泉水', '食品饮料', '饮用水', '农夫山泉', 2.00, 0.80, '农夫山泉'),
(14, '格力空调 KFR-35', '家电', '空调', '格力', 3499.00, 2200.00, '格力电器'),
(15, '戴森 V15 吸尘器', '家电', '吸尘器', '戴森', 4999.00, 3000.00, '戴森代工厂'),
(16, '飞利浦电动牙刷', '日用品', '个人护理', '飞利浦', 399.00, 180.00, '飞利浦代工厂'),
(17, '蓝月亮洗衣液', '日用品', '洗护', '蓝月亮', 49.00, 25.00, '蓝月亮工厂'),
(18, '维达纸巾 3层', '日用品', '纸品', '维达', 29.00, 15.00, '维达纸业'),
(19, 'Nintendo Switch OLED', '电子产品', '游戏机', '任天堂', 2599.00, 2000.00, '鸿海精密'),
(20, '海信55寸电视', '家电', '电视', '海信', 3999.00, 2800.00, '海信集团');

-- ── 4. 客户维度 ──────────────────────────────────────────────
CREATE TABLE dim_customer (
    customer_id INTEGER PRIMARY KEY,
    customer_name VARCHAR(20),
    customer_tier VARCHAR(10),
    industry VARCHAR(20),
    city VARCHAR(20),
    register_date DATE
);

INSERT INTO dim_customer VALUES
(1, '张伟', '钻石', '互联网', '北京', '2023-01-15'),
(2, '李娜', '金卡', '金融', '上海', '2023-03-20'),
(3, '王强', '普通', '教育', '广州', '2023-05-10'),
(4, '赵丽', '银卡', '医疗', '深圳', '2023-06-01'),
(5, '陈明', '金卡', '房地产', '杭州', '2023-08-15'),
(6, '刘洋', '普通', '制造业', '成都', '2023-09-01'),
(7, '孙悦', '钻石', '互联网', '南京', '2024-01-10'),
(8, '周杰', '银卡', '零售', '重庆', '2024-02-05'),
(9, '吴芳', '普通', '政府', '西安', '2024-03-01'),
(10, '郑浩', '金卡', '金融', '天津', '2024-03-15'),
(11, '王敏', '银卡', '教育', '武汉', '2024-04-01'),
(12, '李婷', '普通', '医疗', '长沙', '2024-04-15');

-- ── 5. 仓库维度 ──────────────────────────────────────────────
CREATE TABLE dim_warehouse (
    warehouse_id INTEGER PRIMARY KEY,
    warehouse_name VARCHAR(20),
    city VARCHAR(20),
    capacity INTEGER
);

INSERT INTO dim_warehouse VALUES
(1, '北京中心仓', '北京', 50000),
(2, '上海中心仓', '上海', 60000),
(3, '广州中心仓', '广州', 45000),
(4, '成都中心仓', '成都', 35000),
(5, '西安分仓', '西安', 15000);

-- ── 6. 员工维度 ──────────────────────────────────────────────
CREATE TABLE dim_employee (
    employee_id INTEGER PRIMARY KEY,
    employee_name VARCHAR(20),
    department VARCHAR(20),
    position VARCHAR(20),
    city VARCHAR(20),
    hire_date DATE
);

INSERT INTO dim_employee VALUES
(1, '张三', '销售部', '销售经理', '北京', '2020-03-01'),
(2, '李四', '销售部', '销售代表', '上海', '2021-06-15'),
(3, '王五', '销售部', '销售代表', '广州', '2021-09-01'),
(4, '赵六', '销售部', '销售代表', '成都', '2022-01-10'),
(5, '钱七', '销售部', '销售经理', '深圳', '2022-03-20'),
(6, '孙八', '市场部', '市场总监', '北京', '2020-01-01'),
(7, '周九', '市场部', '市场专员', '上海', '2023-02-15'),
(8, '吴十', '运营部', '运营主管', '杭州', '2021-11-01');

-- ── 7. 销售事实 ──────────────────────────────────────────────
CREATE TABLE fact_sales (
    sale_id INTEGER PRIMARY KEY,
    date_id INTEGER,
    product_id INTEGER,
    region_id INTEGER,
    customer_id INTEGER,
    channel VARCHAR(10),
    quantity INTEGER,
    unit_price DECIMAL(10,2),
    discount DECIMAL(4,2),
    total_amount DECIMAL(12,2),
    cost_amount DECIMAL(12,2),
    profit DECIMAL(12,2)
);

INSERT INTO fact_sales VALUES
(1, 20240115, 1, 4, 4, '线上', 2, 7999.00, 0.00, 15998.00, 11000.00, 4998.00),
(2, 20240116, 2, 8, 2, '线上', 3, 6999.00, 0.05, 19947.15, 13680.00, 6267.15),
(3, 20240117, 7, 8, 3, '线下', 5, 1299.00, 0.00, 6495.00, 3000.00, 3495.00),
(4, 20240201, 14, 1, 1, '线下', 1, 3499.00, 0.10, 3149.10, 1980.00, 1169.10),
(5, 20240210, 3, 5, 5, '线上', 1, 10999.00, 0.00, 10999.00, 7500.00, 3499.00),
(6, 20240211, 17, 6, 6, '线上', 100, 49.00, 0.00, 4900.00, 2500.00, 2400.00),
(7, 20240212, 12, 1, 10, '线下', 200, 8.00, 0.00, 1600.00, 700.00, 900.00),
(8, 20240301, 9, 2, 7, '线上', 3, 799.00, 0.00, 2397.00, 1050.00, 1347.00),
(9, 20240305, 6, 2, 1, '线上', 5, 1899.00, 0.00, 9495.00, 6000.00, 3495.00),
(10, 20240306, 18, 11, 8, '线上', 200, 29.00, 0.00, 5800.00, 3000.00, 2800.00),
(11, 20240307, 4, 12, 6, '线下', 1, 9999.00, 0.08, 9199.08, 6256.00, 2943.08),
(12, 20240315, 5, 7, 11, '线上', 2, 2499.00, 0.00, 4998.00, 3200.00, 1798.00),
(13, 20240320, 16, 8, 9, '线下', 10, 399.00, 0.00, 3990.00, 1800.00, 2190.00),
(14, 20240328, 19, 14, 12, '线上', 2, 2599.00, 0.00, 5198.00, 4000.00, 1198.00),
(15, 20240401, 9, 8, 3, '线上', 5, 799.00, 0.00, 3995.00, 1750.00, 2245.00),
(16, 20240402, 10, 5, 5, '线下', 2, 1599.00, 0.15, 2718.30, 1190.00, 1528.30),
(17, 20240403, 1, 4, 2, '线上', 1, 7999.00, 0.00, 7999.00, 5500.00, 2499.00),
(18, 20240410, 11, 9, 1, '线下', 2, 1499.00, 0.00, 2998.00, 1600.00, 1398.00),
(19, 20240501, 20, 8, 2, '线下', 1, 3999.00, 0.20, 3199.20, 2240.00, 959.20),
(20, 20240505, 7, 10, 3, '线上', 10, 1299.00, 0.00, 12990.00, 6000.00, 6990.00),
(21, 20240510, 13, 1, 10, '线上', 500, 2.00, 0.00, 1000.00, 400.00, 600.00),
(22, 20240511, 8, 4, 4, '线下', 3, 1199.00, 0.00, 3597.00, 1650.00, 1947.00),
(23, 20240512, 4, 2, 9, '线上', 1, 9999.00, 0.00, 9999.00, 6800.00, 3199.00),
(24, 20240601, 3, 11, 6, '线上', 1, 10999.00, 0.05, 10449.05, 7125.00, 3324.05),
(25, 20240602, 19, 4, 5, '线上', 1, 2599.00, 0.00, 2599.00, 2000.00, 599.00),
(26, 20240603, 2, 8, 2, '线上', 2, 6999.00, 0.00, 13998.00, 9600.00, 4398.00),
(27, 20240615, 15, 7, 11, '线下', 1, 4999.00, 0.10, 4499.10, 2700.00, 1799.10),
(28, 20240620, 6, 5, 7, '线上', 3, 1899.00, 0.00, 5697.00, 3600.00, 2097.00),
(29, 20240701, 1, 9, 1, '线下', 2, 7999.00, 0.00, 15998.00, 11000.00, 4998.00),
(30, 20240710, 3, 8, 7, '线上', 1, 10999.00, 0.00, 10999.00, 7500.00, 3499.00),
(31, 20240711, 18, 2, 8, '线上', 150, 29.00, 0.00, 4350.00, 2250.00, 2100.00),
(32, 20240712, 14, 4, 12, '线下', 2, 3499.00, 0.00, 6998.00, 4400.00, 2598.00),
(33, 20240801, 16, 1, 3, '线上', 8, 399.00, 0.00, 3192.00, 1440.00, 1752.00),
(34, 20240802, 7, 8, 5, '线上', 6, 1299.00, 0.00, 7794.00, 3600.00, 4194.00),
(35, 20240803, 5, 2, 10, '线上', 3, 2499.00, 0.00, 7497.00, 4800.00, 2697.00),
(36, 20240810, 2, 11, 4, '线下', 1, 6999.00, 0.00, 6999.00, 4800.00, 2199.00),
(37, 20240820, 10, 9, 1, '线上', 1, 1599.00, 0.00, 1599.00, 700.00, 899.00),
(38, 20240825, 3, 8, 12, '线上', 2, 10999.00, 0.00, 21998.00, 15000.00, 6998.00),
(39, 20240901, 1, 1, 1, '线下', 3, 7999.00, 0.00, 23997.00, 16500.00, 7497.00),
(40, 20241001, 11, 1, 1, '线上', 6, 1499.00, 0.00, 8994.00, 4800.00, 4194.00),
(41, 20241002, 9, 2, 7, '线下', 8, 799.00, 0.00, 6392.00, 2800.00, 3592.00),
(42, 20241002, 4, 4, 5, '线上', 1, 9999.00, 0.00, 9999.00, 6800.00, 3199.00),
(43, 20241101, 2, 8, 3, '线上', 2, 6999.00, 0.10, 12598.20, 8640.00, 3958.20),
(44, 20241110, 7, 9, 4, '线上', 8, 1299.00, 0.00, 10392.00, 4800.00, 5592.00),
(45, 20241201, 20, 5, 5, '线下', 1, 3999.00, 0.00, 3999.00, 2800.00, 1199.00),
(46, 20241220, 3, 4, 2, '线上', 1, 10999.00, 0.00, 10999.00, 7500.00, 3499.00),
(47, 20241225, 6, 11, 6, '线上', 4, 1899.00, 0.00, 7596.00, 4800.00, 2796.00),
(48, 20241231, 1, 8, 2, '线上', 1, 7999.00, 0.00, 7999.00, 5500.00, 2499.00);

-- ── 8. 退货事实 ──────────────────────────────────────────────
CREATE TABLE fact_returns (
    return_id INTEGER PRIMARY KEY,
    sale_id INTEGER,
    product_id INTEGER,
    return_date_id INTEGER,
    return_quantity INTEGER,
    return_amount DECIMAL(10,2),
    return_reason VARCHAR(20),
    handling VARCHAR(10)
);

INSERT INTO fact_returns VALUES
(1, 1, 1, 20240220, 1, 7999.00, '质量问题', '退款'),
(2, 2, 2, 20240315, 1, 6649.05, '不适用', '退款'),
(3, 5, 3, 20240320, 1, 10999.00, '运输损坏', '换货'),
(4, 9, 6, 20240410, 1, 1899.00, '描述不符', '退款'),
(5, 12, 5, 20240415, 1, 2499.00, '质量问题', '换货'),
(6, 16, 10, 20240501, 1, 1359.15, '不适用', '退款'),
(7, 20, 7, 20240601, 2, 2598.00, '质量问题', '退款'),
(8, 26, 2, 20240620, 1, 6999.00, '运输损坏', '换货'),
(9, 29, 1, 20240720, 1, 7999.00, '质量问题', '退款'),
(10, 34, 7, 20240815, 1, 1299.00, '不适用', '退款'),
(11, 38, 3, 20240905, 1, 10999.00, '描述不符', '退款'),
(12, 43, 2, 20241115, 1, 6299.10, '质量问题', '换货');

-- ── 9. 库存事实 ──────────────────────────────────────────────
CREATE TABLE fact_inventory (
    inventory_id INTEGER PRIMARY KEY,
    product_id INTEGER,
    warehouse_id INTEGER,
    date_id INTEGER,
    quantity_on_hand INTEGER,
    quantity_reserved INTEGER,
    quantity_available INTEGER
);

INSERT INTO fact_inventory VALUES
(1, 1, 1, 20240101, 200, 20, 180),
(2, 2, 1, 20240101, 150, 15, 135),
(3, 3, 1, 20240101, 100, 10, 90),
(4, 1, 1, 20240401, 80, 10, 70),
(5, 2, 1, 20240401, 120, 15, 105),
(6, 3, 1, 20240401, 200, 20, 180),
(7, 1, 1, 20240701, 50, 5, 45),
(8, 2, 1, 20240701, 30, 5, 25),
(9, 3, 1, 20240701, 10, 2, 8),
(10, 4, 1, 20240701, 5, 1, 4),
(11, 2, 2, 20240101, 300, 30, 270),
(12, 2, 2, 20240401, 250, 25, 225),
(13, 2, 2, 20240701, 200, 20, 180),
(14, 1, 3, 20240101, 80, 5, 75),
(15, 1, 3, 20240401, 60, 5, 55),
(16, 1, 3, 20240701, 40, 5, 35),
(17, 2, 6, 20240101, 500, 50, 450),
(18, 2, 6, 20240401, 350, 30, 320),
(19, 2, 6, 20240701, 200, 20, 180),
(20, 3, 9, 20240101, 400, 40, 360),
(21, 3, 9, 20240401, 300, 30, 270),
(22, 3, 9, 20240701, 150, 20, 130),
(23, 1, 7, 20240101, 100, 10, 90),
(24, 1, 7, 20240701, 20, 5, 15),
(25, 2, 11, 20240101, 60, 5, 55),
(26, 2, 11, 20241001, 120, 10, 110),
(27, 1, 14, 20240101, 30, 5, 25),
(28, 1, 14, 20240701, 10, 2, 8),
(29, 3, 18, 20240101, 1000, 100, 900),
(30, 3, 18, 20240701, 800, 80, 720);

-- ── 10. 考勤事实 ─────────────────────────────────────────────
CREATE TABLE fact_attendance (
    attendance_id INTEGER PRIMARY KEY,
    employee_id INTEGER,
    date_id INTEGER,
    status VARCHAR(10),
    work_hours DECIMAL(4,1)
);

INSERT INTO fact_attendance VALUES
(1, 1, 20240301, '正常', 8.0),
(2, 2, 20240301, '正常', 8.0),
(3, 3, 20240301, '正常', 8.0),
(4, 4, 20240301, '正常', 8.0),
(5, 5, 20240301, '正常', 8.0),
(6, 1, 20240305, '正常', 8.5),
(7, 2, 20240305, '加班', 10.0),
(8, 3, 20240305, '正常', 8.0),
(9, 4, 20240305, '请假', 0.0),
(10, 6, 20240305, '正常', 8.0),
(11, 1, 20240401, '正常', 8.0),
(12, 2, 20240401, '正常', 8.0),
(13, 3, 20240401, '正常', 8.0),
(14, 4, 20240401, '请假', 0.0),
(15, 7, 20240401, '正常', 8.0),
(16, 1, 20240501, '正常', 8.0),
(17, 2, 20240501, '加班', 11.0),
(18, 3, 20240501, '正常', 8.0),
(19, 5, 20240501, '正常', 8.0),
(20, 8, 20240501, '正常', 8.0);

-- ── 字段语义注释（数据字典 RAG 桥的权威语义源，mcp_server.introspect 读取） ──

COMMENT ON TABLE dim_date IS '日期维度表，包含年/季度/月/周以及节假日标记';
COMMENT ON COLUMN dim_date.date_id IS '日期主键，格式 yyyymmdd';
COMMENT ON COLUMN dim_date.full_date IS '完整日期';
COMMENT ON COLUMN dim_date.year IS '年份';
COMMENT ON COLUMN dim_date.quarter_num IS '季度序号（1-4）';
COMMENT ON COLUMN dim_date.quarter IS '季度标签（Q1-Q4）';
COMMENT ON COLUMN dim_date.week_of_year IS '年内周数';
COMMENT ON COLUMN dim_date.day_name IS '星期（中文）';
COMMENT ON COLUMN dim_date.is_holiday IS '节假日标记：0=工作日，1=节假日';

COMMENT ON TABLE dim_region IS '区域和城市映射表，包含大区及对应省市';
COMMENT ON COLUMN dim_region.region_id IS '区域主键';
COMMENT ON COLUMN dim_region.region_name IS '大区名称（华北/华东/华南/西南/西北/东北）';
COMMENT ON COLUMN dim_region.province IS '省份';
COMMENT ON COLUMN dim_region.city IS '城市';
COMMENT ON COLUMN dim_region.tier IS '城市等级（一线/二线/三线）';

COMMENT ON TABLE dim_product IS '产品信息表，包含品类、品牌与价格';
COMMENT ON COLUMN dim_product.product_id IS '产品主键';
COMMENT ON COLUMN dim_product.product_name IS '产品名称';
COMMENT ON COLUMN dim_product.category IS '产品大类（电子产品/服装鞋帽/食品饮料/家电/日用品）';
COMMENT ON COLUMN dim_product.sub_category IS '产品子品类';
COMMENT ON COLUMN dim_product.brand IS '品牌';
COMMENT ON COLUMN dim_product.unit_price IS '单价（元）';
COMMENT ON COLUMN dim_product.cost_price IS '成本价（元）';
COMMENT ON COLUMN dim_product.supplier IS '供应商';

COMMENT ON TABLE dim_customer IS '客户维度表，包含等级、行业与注册信息';
COMMENT ON COLUMN dim_customer.customer_id IS '客户主键';
COMMENT ON COLUMN dim_customer.customer_name IS '客户名称';
COMMENT ON COLUMN dim_customer.customer_tier IS '客户等级（钻石/金卡/银卡/普通）';
COMMENT ON COLUMN dim_customer.industry IS '所属行业';
COMMENT ON COLUMN dim_customer.city IS '所在城市';
COMMENT ON COLUMN dim_customer.register_date IS '注册日期';

COMMENT ON TABLE dim_warehouse IS '仓库维度表';
COMMENT ON COLUMN dim_warehouse.warehouse_id IS '仓库主键';
COMMENT ON COLUMN dim_warehouse.warehouse_name IS '仓库名称';
COMMENT ON COLUMN dim_warehouse.city IS '所在城市';
COMMENT ON COLUMN dim_warehouse.capacity IS '容量上限（件）';

COMMENT ON TABLE dim_employee IS '员工维度表';
COMMENT ON COLUMN dim_employee.employee_id IS '员工主键';
COMMENT ON COLUMN dim_employee.employee_name IS '员工姓名';
COMMENT ON COLUMN dim_employee.department IS '部门';
COMMENT ON COLUMN dim_employee.position IS '岗位';
COMMENT ON COLUMN dim_employee.city IS '工作城市';
COMMENT ON COLUMN dim_employee.hire_date IS '入职日期';

COMMENT ON TABLE fact_sales IS '销售记录事实表，每条记录代表一笔销售';
COMMENT ON COLUMN fact_sales.sale_id IS '销售记录主键';
COMMENT ON COLUMN fact_sales.date_id IS '销售日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_sales.product_id IS '产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_sales.region_id IS '区域（关联 dim_region.region_id）';
COMMENT ON COLUMN fact_sales.customer_id IS '客户（关联 dim_customer.customer_id）';
COMMENT ON COLUMN fact_sales.channel IS '销售渠道（线上/线下）';
COMMENT ON COLUMN fact_sales.quantity IS '销售数量';
COMMENT ON COLUMN fact_sales.unit_price IS '成交单价（元）';
COMMENT ON COLUMN fact_sales.discount IS '折扣率（如 0.90 表示九折）';
COMMENT ON COLUMN fact_sales.total_amount IS '销售金额（元），等于 quantity × unit_price × discount';
COMMENT ON COLUMN fact_sales.cost_amount IS '成本金额（元）';
COMMENT ON COLUMN fact_sales.profit IS '毛利（元），等于 total_amount − cost_amount';

COMMENT ON TABLE fact_returns IS '退货记录事实表，关联销售记录';
COMMENT ON COLUMN fact_returns.return_id IS '退货记录主键';
COMMENT ON COLUMN fact_returns.sale_id IS '关联销售记录（关联 fact_sales.sale_id）';
COMMENT ON COLUMN fact_returns.product_id IS '退货产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_returns.return_date_id IS '退货日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_returns.return_quantity IS '退货数量';
COMMENT ON COLUMN fact_returns.return_amount IS '退货金额（元）';
COMMENT ON COLUMN fact_returns.return_reason IS '退货原因（质量问题/不适用/运输损坏/描述不符）';
COMMENT ON COLUMN fact_returns.handling IS '处理方式（退款/换货）';

COMMENT ON TABLE fact_inventory IS '库存记录事实表，按产品+仓库+日期记录';
COMMENT ON COLUMN fact_inventory.inventory_id IS '库存记录主键';
COMMENT ON COLUMN fact_inventory.product_id IS '产品（关联 dim_product.product_id）';
COMMENT ON COLUMN fact_inventory.warehouse_id IS '仓库（关联 dim_warehouse.warehouse_id）';
COMMENT ON COLUMN fact_inventory.date_id IS '快照日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_inventory.quantity_on_hand IS '在库数量';
COMMENT ON COLUMN fact_inventory.quantity_reserved IS '预留数量';
COMMENT ON COLUMN fact_inventory.quantity_available IS '可售数量，等于 quantity_on_hand − quantity_reserved';

COMMENT ON TABLE fact_attendance IS '考勤记录事实表，关联员工';
COMMENT ON COLUMN fact_attendance.attendance_id IS '考勤记录主键';
COMMENT ON COLUMN fact_attendance.employee_id IS '员工（关联 dim_employee.employee_id）';
COMMENT ON COLUMN fact_attendance.date_id IS '考勤日期（关联 dim_date.date_id）';
COMMENT ON COLUMN fact_attendance.status IS '考勤状态（正常/请假 等）';
COMMENT ON COLUMN fact_attendance.work_hours IS '工时（小时）';
