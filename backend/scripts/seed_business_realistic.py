"""真实化零售星型模型 seed（2023–2025，约 18 万订单）。

背景
----
原 `seed_business_p15prelude.sql` 只覆盖 2024 一年、3 万订单、均匀随机分布：
无季节峰、无复购结构、退款率 15% 偏高、支付与订单一一对齐且全在当天。
做需求理解 / SQL 生成 / 图表选择的演示与评测时，"均匀随机"会让所有查询都
长得一样（每个月都平、每个门店都差不多），看不出分析能力。

本脚本重建 `public` 星型模型，保持 **表名 / 列名 / 取值域完全不变**（前端需求
选项、SQL 表白名单、评测用例都依赖这些字面值），只把分布改成真实零售形态：

1. **时间**：2023-01-01 ~ 2025-12-31 连续三年（支持同比/环比/多年趋势）。
2. **季节性**：618 与双 11 明显峰、春节月回落、周末高于工作日、节假日抬升。
3. **门店**：旗舰店/标准店权重高、快闪店只在其开业后参与、各区域门店数不均。
4. **商品**：品类销量与单价反相关（配件高频低价、手机低频高价），价格带真实。
5. **客户**：个人/企业/政府三分，企业大单多件数、个人 1~3 件，复购服从长尾。
6. **促销**：只在促销窗口内的订单挂 `promotion_id`，`order_amount` 按
   `discount_rate`（实付比例，如 0.70 = 打七折）折算。
7. **支付**：绝大多数 SUCCESS；30 天以前的订单约 6% REFUNDED；
   最近 14 天内约 8% PENDING（尚未结算）；支付日期 = 订单日或 +1/+2 天。

约束
----
- 幂等：先清空 fact / dim 再重灌，可重复执行。
- 确定性：`random.seed(SEED)`，同样参数重跑得到同样数据（便于评测复现）。
- 只写 `public` schema；不触碰 app / agent / memory / observability。

用法
----
    D:\\miniConda\\envs\\agent\\python.exe backend/scripts/seed_business_realistic.py
    add --dry-run 只打印计划不落库；--orders 60000 可改总量级。

注意：本脚本不替代 `seed_business_p15prelude.sql`（那是 30k 精简版，文档仍引用它）；
两者选一执行，后执行的会覆盖前者。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

SEED = 42
START_DATE = dt.date(2023, 1, 1)
END_DATE = dt.date(2026, 10, 31)
# "今天" = 数据末端 + 1 天：用于判定 PENDING/REFUNDED，保证数据自洽且可复现。
AS_OF = dt.date(2026, 11, 1)
YEARS = tuple(range(START_DATE.year, END_DATE.year + 1))

REGIONS = ["华东", "华北", "华南", "华中", "西南", "西北"]
REGION_CITIES = {
    "华东": ["上海", "杭州", "南京", "苏州"],
    "华北": ["北京", "天津", "石家庄"],
    "华南": ["广州", "深圳", "东莞"],
    "华中": ["武汉", "长沙", "郑州"],
    "西南": ["成都", "重庆", "昆明"],
    "西北": ["西安", "兰州"],
}
# 各区域门店数（华东最密，西北最疏）+ 区域销售额权重
REGION_STORE_COUNT = {"华东": 10, "华北": 8, "华南": 7, "华中": 6, "西南": 5, "西北": 4}
REGION_WEIGHT = {"华东": 0.30, "华北": 0.24, "华南": 0.20, "华中": 0.11, "西南": 0.10, "西北": 0.05}

STORE_TYPES = ["旗舰店", "标准店", "体验店", "加盟店", "快闪店"]
STORE_TYPE_WEIGHT = {"旗舰店": 3.4, "标准店": 2.0, "体验店": 1.2, "加盟店": 1.6, "快闪店": 0.5}
MALLS = ["万象城", "大悦城", "银泰中心", "龙湖天街", "印象城", "恒隆广场", "国金中心", "凯德MALL"]

# (品牌, 型号, 参考价)
PRODUCT_LINES: dict[str, list[tuple[str, str, float]]] = {
    "手机": [
        ("华为", "Mate 60 Pro 12+512G", 6999), ("华为", "P60 Pro 8+256G", 4988),
        ("华为", "nova 12 8+256G", 2999), ("Apple", "iPhone 15 Pro Max 256G", 9999),
        ("Apple", "iPhone 15 128G", 5999), ("Apple", "iPhone 14 128G", 4799),
        ("小米", "14 Ultra 16+512G", 6499), ("小米", "14 12+256G", 4299),
        ("小米", "Redmi K70 12+256G", 2499), ("OPPO", "Find X7 12+256G", 4999),
        ("OPPO", "Reno 11 8+256G", 2799), ("vivo", "X100 Pro 16+512G", 5999),
        ("vivo", "S18 8+256G", 2499), ("三星", "Galaxy S24 Ultra 12+512G", 9699),
        ("三星", "Galaxy A55 8+256G", 2299), ("荣耀", "Magic6 Pro 12+512G", 5699),
        ("荣耀", "荣耀100 12+256G", 2499), ("一加", "Ace 3 16+512G", 2999),
    ],
    "平板": [
        ("华为", "MatePad Pro 13.2 12+512G", 6999), ("华为", "MatePad 11.5 8+256G", 2299),
        ("Apple", "iPad Pro 11 M4 256G", 8499), ("Apple", "iPad Air 11 128G", 4799),
        ("Apple", "iPad 10 64G", 2999), ("小米", "Pad 6 Pro 8+256G", 2599),
        ("小米", "Pad 6 6+128G", 1599), ("三星", "Tab S9 12+256G", 6999),
        ("三星", "Tab A9 4+64G", 1299), ("OPPO", "Pad 2 8+256G", 2299),
        ("vivo", "Pad 3 8+256G", 2199), ("荣耀", "平板 V8 Pro 8+256G", 2599),
        ("联想", "小新 Pad Pro 12.7 8+256G", 1999), ("联想", "Y700 12+256G", 2499),
        ("华为", "MatePad Air 8+256G", 3299), ("红米", "Pad SE 6+128G", 899),
    ],
    "耳机": [
        ("Apple", "AirPods Pro 2", 1899), ("Apple", "AirPods 4", 1299),
        ("华为", "FreeBuds Pro 3", 1499), ("华为", "FreeBuds 6i", 499),
        ("小米", "Buds 5 Pro", 999), ("小米", "Redmi Buds 5", 199),
        ("Sony", "WH-1000XM5", 2899), ("Sony", "WF-1000XM5", 2299),
        ("Bose", "QuietComfort Ultra", 3199), ("Bose", "QC Earbuds II", 2299),
        ("三星", "Buds3 Pro", 1299), ("OPPO", "Enco X3", 899),
        ("vivo", "TWS 4 Pro", 799), ("JBL", "TUNE 770NC", 799),
        ("漫步者", "NeoBuds Pro 2", 599), ("捷波朗", "Elite 8", 1599),
    ],
    "音箱": [
        ("Apple", "HomePod mini", 749), ("Apple", "HomePod 2", 2299),
        ("华为", "Sound X", 1999), ("小米", "小爱音箱 Pro", 349),
        ("小米", "Sound Pro", 999), ("Sony", "SRS-XB100", 499),
        ("Sony", "HT-A5000 回音壁", 4499), ("Bose", "SoundLink Flex", 1299),
        ("Bose", "SoundLink Max", 2999), ("JBL", "Charge 5", 1099),
        ("JBL", "Flip 6", 799), ("哈曼卡顿", "Aura Studio 4", 2199),
        ("Marshall", "Emberton II", 1499), ("三星", "HW-Q600C 回音壁", 2599),
        ("漫步者", "S2000MKIII", 1899), ("Sonos", "Era 100", 2299),
    ],
    "智能手表": [
        ("Apple", "Watch Series 9 45mm", 3199), ("Apple", "Watch Ultra 2", 6499),
        ("Apple", "Watch SE 2", 1999), ("华为", "Watch GT 4", 1688),
        ("华为", "Watch 4 Pro", 2999), ("华为", "WATCH Ultimate", 8480),
        ("小米", "Watch S3", 999), ("小米", "手环 8 Pro", 399),
        ("三星", "Galaxy Watch6 44mm", 2299), ("OPPO", "Watch 4 Pro", 1999),
        ("vivo", "Watch 3", 1299), ("荣耀", "手表 GS 4", 1499),
        ("佳明", "Forerunner 265", 3599), ("佳明", "Instinct 2", 2499),
        ("Amazfit", "GTR 4", 1299), ("Amazfit", "T-Rex 3", 1599),
    ],
    "配件": [
        ("Apple", "MagSafe 充电器", 399), ("Apple", "20W 电源适配器", 149),
        ("Apple", "USB-C 编织线 1m", 149), ("华为", "66W 超级快充套装", 299),
        ("华为", "车载无线充", 399), ("小米", "65W 氮化镓充电器", 129),
        ("小米", "移动电源 20000mAh", 199), ("Anker", "140W 四口充电器", 599),
        ("Anker", "磁吸无线充电宝", 399), ("倍思", "100W 数据线", 69),
        ("绿联", "Type-C 扩展坞 8in1", 499), ("罗技", "MX Master 3S", 799),
        ("罗技", "K380 键盘", 199), ("Sony", "128G 存储卡", 299),
        ("小米", "无线鼠标", 79), ("绿联", "钢化膜两片装", 39),
    ],
}
CATEGORY_WEIGHT = {"配件": 0.30, "耳机": 0.22, "智能手表": 0.15, "平板": 0.12, "音箱": 0.11, "手机": 0.10}

CUSTOMER_TYPES = [("个人", 0.75), ("企业", 0.20), ("政府", 0.05)]
VIP_LEVELS = [("普通", 0.55), ("银卡", 0.25), ("金卡", 0.15), ("钻石", 0.05)]
PAYMENT_METHODS = ["微信", "支付宝", "银行卡", "现金"]
SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁")
GIVEN = ["先生", "女士", "经理", "老师", "医生"]

# (促销名, 起始月日, 结束月日, 折扣率)
FESTIVALS = [
    ("春节年货节", (1, 15), (2, 5), 0.85),
    ("春季新品发布", (3, 10), (3, 31), 0.90),
    ("618 年中庆", (6, 1), (6, 18), 0.72),
    ("暑期学生季", (7, 1), (8, 20), 0.85),
    ("99 购物节", (9, 1), (9, 9), 0.80),
    ("国庆黄金周", (9, 28), (10, 7), 0.82),
    ("双11 全球狂欢节", (10, 25), (11, 11), 0.65),
    ("双12 年终盛典", (12, 1), (12, 12), 0.75),
]

MONTH_FACTOR = {1: 1.05, 2: 0.82, 3: 0.92, 4: 0.94, 5: 1.02, 6: 1.28,
                7: 0.98, 8: 1.00, 9: 0.96, 10: 1.06, 11: 1.42, 12: 1.18}
WEEKDAY_FACTOR = {0: 0.92, 1: 0.92, 2: 0.96, 3: 1.00, 4: 1.15, 5: 1.36, 6: 1.26}
# 每年日单量基线（≈ 年订单量 / 365）
YEAR_BASE_DAILY = {2023: 150, 2024: 172, 2025: 164, 2026: 170}


def _holidays(year: int) -> set[tuple[int, int]]:
    """法定假日近似（够用即可，用于 dim_date.is_holiday 与当日抬升）。"""
    days: set[tuple[int, int]] = set()
    for m, d in [(1, 1)]:                                    # 元旦
        days.add((m, d))
    spring = {2023: (1, 22), 2024: (2, 10), 2025: (1, 29), 2026: (2, 17)}.get(year, (2, 10))
    s = dt.date(year, *spring)
    for i in range(7):
        x = s + dt.timedelta(days=i)
        days.add((x.month, x.day))
    for m, d, n in [(4, 4, 3), (5, 1, 5), (6, 10, 3), (9, 17, 3), (10, 1, 7)]:
        try:
            s = dt.date(year, m, d)
        except ValueError:
            continue
        for i in range(n):
            x = s + dt.timedelta(days=i)
            days.add((x.month, x.day))
    return days


def _promo_windows() -> list[dict]:
    out = []
    for year in YEARS:
        for name, (sm, sd), (em, ed), rate in FESTIVALS:
            start = dt.date(year, sm, sd)
            if start > END_DATE:
                continue  # 数据末端之后的活动不建（避免促销永远命中不到订单）
            out.append({
                "name": f"{name} {year}",
                "start": start,
                "end": min(dt.date(year, em, ed), END_DATE),
                "rate": rate,
            })
    return out


def build_dims(rng: random.Random) -> dict:
    # --- dim_date ---
    dates = []
    d, did = START_DATE, 0
    year_holidays = {y: _holidays(y) for y in YEARS}
    while d <= END_DATE:
        did += 1
        dates.append((
            int(d.strftime("%Y%m%d")), d, d.year,
            (d.month - 1) // 3 + 1, f"Q{(d.month - 1) // 3 + 1}",
            d.month, int(d.strftime("%W")) + 1,
            ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()],
            1 if (d.month, d.day) in year_holidays[d.year] else 0,
        ))
        d += dt.timedelta(days=1)

    # --- dim_store ---
    stores = []
    sid = 0
    for region in REGIONS:
        cities = REGION_CITIES[region]
        for i in range(REGION_STORE_COUNT[region]):
            sid += 1
            city = cities[i % len(cities)]
            stype = STORE_TYPES[i % len(STORE_TYPES)] if i >= 2 else ("旗舰店" if i == 0 else "标准店")
            mall = MALLS[(i + len(city)) % len(MALLS)]
            open_date = dt.date(2018 + (i % 4), (i % 12) + 1, (i * 7) % 27 + 1)
            if stype == "快闪店":
                open_date = dt.date(2023, (i % 12) + 1, (i * 5) % 27 + 1)
            stores.append((sid, f"{city}{mall}{stype}", region, city, stype, open_date))

    # --- dim_product ---
    products = []
    pid = 0
    for category, lines in PRODUCT_LINES.items():
        for brand, model, price in lines:
            pid += 1
            jitter = rng.uniform(-0.06, 0.06)
            products.append((
                pid, f"{brand} {model}", category, brand,
                round(price * (1 + jitter), 2),
            ))

    # --- dim_customer ---
    customers = []
    region_pool = [r for r, w in REGION_WEIGHT.items() for _ in range(max(1, int(w * 100)))]
    for cid in range(1, 20001):
        ctype = rng.choices([t for t, _ in CUSTOMER_TYPES], [w for _, w in CUSTOMER_TYPES])[0]
        region = rng.choice(region_pool)
        vip = rng.choices([v for v, _ in VIP_LEVELS], [w for _, w in VIP_LEVELS])[0]
        if ctype == "个人":
            name = f"{rng.choice(SURNAMES)}{rng.choice(GIVEN)}"
        elif ctype == "企业":
            name = f"{rng.choice(['恒信', '远大', '中科', '华胜', '鼎盛', '启明'])}{rng.choice(['科技', '商贸', '物流', '实业'])}有限公司"
        else:
            name = f"{rng.choice(['市', '区', '县'])}{rng.choice(['财政', '教育', '卫生', '交通'])}局"
        customers.append((cid, name, ctype, region, vip))

    # --- dim_promotion ---
    promotions = []
    for i, w in enumerate(_promo_windows(), start=1):
        promotions.append((i, w["name"], w["rate"], w["start"], w["end"]))

    return {"dates": dates, "stores": stores, "products": products,
            "customers": customers, "promotions": promotions,
            "windows": _promo_windows()}


def build_facts(rng: random.Random, dims: dict, target_orders: int) -> tuple[list, list]:
    stores = dims["stores"]
    products = dims["products"]
    customers = dims["customers"]
    windows = dims["windows"]

    store_w = {s[0]: STORE_TYPE_WEIGHT[s[4]] * REGION_WEIGHT[s[2]] * 3 for s in stores}
    store_ids = list(store_w)
    store_weights = [store_w[i] for i in store_ids]

    prod_by_cat: dict[str, list] = {}
    for p in products:
        prod_by_cat.setdefault(p[2], []).append(p)
    cats = list(CATEGORY_WEIGHT)
    cat_weights = [CATEGORY_WEIGHT[c] for c in cats]

    cust_by_type: dict[str, list] = {}
    for c in customers:
        cust_by_type.setdefault(c[2], []).append(c)

    # 促销按日索引：date -> (promotion_id, rate)
    promo_of_day: dict[dt.date, tuple[int, float]] = {}
    for i, w in enumerate(windows, start=1):
        d = w["start"]
        while d <= w["end"]:
            promo_of_day[d] = (i, w["rate"])
            d += dt.timedelta(days=1)

    # 每日目标单量
    per_day: list[tuple[dt.date, int]] = []
    for year, base in YEAR_BASE_DAILY.items():
        d = dt.date(year, 1, 1)
        while d.year == year and d <= END_DATE:
            n = base * MONTH_FACTOR[d.month] * WEEKDAY_FACTOR[d.weekday()]
            if d in promo_of_day:
                n *= 1.55
            if (d.month, d.day) in _holidays(year):
                n *= 1.18
            per_day.append((d, max(1, int(round(n * rng.uniform(0.9, 1.1))))))
            d += dt.timedelta(days=1)

    total_planned = sum(n for _, n in per_day)
    scale = target_orders / total_planned if total_planned else 1.0

    orders: list[tuple] = []
    payments: list[tuple] = []
    oid = 0
    pay_id = 0
    for day, n in per_day:
        n = max(1, int(round(n * scale)))
        # 快闪店开业前的订单不能挂它
        eligible = [s for s in store_ids if stores[s - 1][5] <= day] or store_ids
        eligible_w = [store_w[s] if s in eligible else 0 for s in store_ids]
        for _ in range(n):
            oid += 1
            store_id = rng.choices(store_ids, eligible_w)[0]
            cat = rng.choices(cats, cat_weights)[0]
            product = rng.choice(prod_by_cat[cat])
            ctype = rng.choices([t for t, _ in CUSTOMER_TYPES], [w for _, w in CUSTOMER_TYPES])[0]
            customer = rng.choice(cust_by_type[ctype])

            if ctype == "企业":
                qty = rng.choice([3, 5, 5, 8, 10, 10, 15, 20])
            elif ctype == "政府":
                qty = rng.choice([2, 3, 4, 5, 6, 8])
            else:
                qty = rng.choices([1, 1, 1, 2, 2, 3], [0.45, 0.2, 0.1, 0.15, 0.07, 0.03])[0]

            promo = promo_of_day.get(day)
            promo_id = promo[0] if promo else None
            rate = promo[1] if promo else 1.0
            amount = round(product[4] * qty * rate, 2)

            if ctype == "企业":
                method = rng.choices(PAYMENT_METHODS, [0.05, 0.10, 0.70, 0.15])[0]
            elif ctype == "政府":
                method = rng.choices(PAYMENT_METHODS, [0.05, 0.05, 0.80, 0.10])[0]
            else:
                method = rng.choices(PAYMENT_METHODS, [0.40, 0.38, 0.18, 0.04])[0]

            orders.append((oid, day, store_id, customer[0], product[0], promo_id,
                           qty, amount, method))

            # --- 支付 ---
            age_days = (AS_OF - day).days
            if age_days <= 14:
                status = rng.choices(["SUCCESS", "PENDING", "REFUNDED"], [0.88, 0.09, 0.03])[0]
            else:
                status = rng.choices(["SUCCESS", "REFUNDED"], [0.94, 0.06])[0]
            lag = rng.choices([0, 1, 2], [0.86, 0.11, 0.03])[0]
            pay_date = day + dt.timedelta(days=lag)
            if pay_date < day:
                pay_date = day
            pay_amount = amount
            if status == "REFUNDED" and rng.random() < 0.45:
                pay_amount = round(amount * rng.choice([0.3, 0.5, 0.7]), 2)
            pay_id += 1
            payments.append((pay_id, oid, pay_date, pay_amount, method, status))

    return orders, payments


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真实化零售星型模型 seed")
    ap.add_argument("--orders", type=int, default=180000, help="目标订单总量（默认 180000）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不落库")
    args = ap.parse_args(argv)

    rng = random.Random(SEED)
    dims = build_dims(rng)
    orders, payments = build_facts(rng, dims, args.orders)

    print(f"dim_date      : {len(dims['dates'])}")
    print(f"dim_store     : {len(dims['stores'])}")
    print(f"dim_product   : {len(dims['products'])}")
    print(f"dim_customer  : {len(dims['customers'])}")
    print(f"dim_promotion : {len(dims['promotions'])}")
    print(f"fact_orders   : {len(orders)}  ({orders[0][1]} ~ {orders[-1][1]})")
    print(f"fact_payments : {len(payments)}")
    if args.dry_run:
        print("dry-run：未写库")
        return 0

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    dsn = line.split("=", 1)[1].strip()
                    break
    if not dsn:
        dsn = "postgresql://ragent:ragent@localhost:5432/ragent"

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '600s'")
            print("清空 fact / dim ...")
            cur.execute("TRUNCATE public.fact_payments, public.fact_orders")
            for t in ("dim_promotion", "dim_customer", "dim_product", "dim_store", "dim_date"):
                cur.execute(f"DELETE FROM public.{t}")

            print("灌 dim ...")
            execute_values(cur,
                "INSERT INTO public.dim_date (date_id, full_date, year, quarter_num, quarter,"
                " month, week_of_year, day_of_week, is_holiday) VALUES %s", dims["dates"])
            execute_values(cur,
                "INSERT INTO public.dim_store (store_id, store_name, region, city, store_type,"
                " open_date) VALUES %s", dims["stores"])
            execute_values(cur,
                "INSERT INTO public.dim_product (product_id, product_name, category, brand,"
                " unit_price) VALUES %s", dims["products"])
            execute_values(cur,
                "INSERT INTO public.dim_customer (customer_id, customer_name, customer_type,"
                " region, vip_level) VALUES %s", dims["customers"])
            execute_values(cur,
                "INSERT INTO public.dim_promotion (promotion_id, promo_name, discount_rate,"
                " start_date, end_date) VALUES %s", dims["promotions"])

            print("灌 fact_orders ...")
            execute_values(cur,
                "INSERT INTO public.fact_orders (order_id, order_date, store_id, customer_id,"
                " product_id, promotion_id, quantity, order_amount, payment_method)"
                " VALUES %s", orders, page_size=5000)
            print("灌 fact_payments ...")
            execute_values(cur,
                "INSERT INTO public.fact_payments (payment_id, order_id, payment_date,"
                " payment_amount, payment_method, status) VALUES %s", payments, page_size=5000)

            print("ANALYZE ...")
            for t in ("fact_orders", "fact_payments", "dim_date", "dim_store",
                      "dim_product", "dim_customer", "dim_promotion"):
                cur.execute(f"ANALYZE public.{t}")

            cur.execute("SELECT count(*), sum(order_amount) FROM public.fact_orders")
            cnt, amt = cur.fetchone()
            cur.execute("SELECT status, count(*) FROM public.fact_payments GROUP BY 1 ORDER BY 2 DESC")
            pay_stats = cur.fetchall()

    print(f"\n完成：{cnt} 订单，GMV {float(amt):,.2f}")
    for st, c in pay_stats:
        print(f"  {st}: {c} ({c / sum(x[1] for x in pay_stats):.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
