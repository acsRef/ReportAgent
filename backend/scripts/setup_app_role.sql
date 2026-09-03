-- setup_app_role.sql
-- 幂等创建 SQL 分析路径的最小权限角色 ragent_readonly——
-- 与 2026-08-04 安全加固 plan 的「Explicitly NOT doing」遗留项配套（详见
-- docs/plans/2026-08-05-pg-role-least-privilege.md）。
--
-- 目的：让 LLM 生成的 SELECT 在非超级用户身份下执行。即便应用层 check_sql_safety
-- 五重闸漏判新版本未知函数/扩展，DB 层权限也会拒绝，深度防御的最后一环。
--
-- 用法（一次性，dev 库）：
--   docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/setup_app_role.sql
--
-- 然后在仓库根 .env 加 ANALYSIS_DSN 行：
--   ANALYSIS_DSN=postgresql://ragent_readonly:ragent_readonly@localhost:5432/ragent
--
-- 生产部署：PASSWORD 改走密钥管理（环境变量 / vault / secrets manager），
--          本脚本的明文 'ragent_readonly' 仅供开发环境。

-- 1. 角色存在性检查
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragent_readonly') THEN
        CREATE ROLE ragent_readonly
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            PASSWORD 'ragent_readonly';
    END IF;
END $$;

-- 2. public schema 的 USAGE
GRANT USAGE ON SCHEMA public TO ragent_readonly;

-- 3. 业务表 SELECT——逐张显式 GRANT，不做 blanket public SELECT。
--    这样后续新建的 pgvector / agent / memory / observability 表默认不被分析路径
--    看见，必须显式追加。表名取自 seed_business_p15prelude.sql（现役零售 schema，
--    7 张星型表；旧 10 表演示库已退役）。
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'dim_date', 'dim_store', 'dim_product', 'dim_customer', 'dim_promotion',
        'fact_orders', 'fact_payments'
    ]
    LOOP
        EXECUTE format('GRANT SELECT ON TABLE public.%I TO ragent_readonly', t);
    END LOOP;
END $$;

-- 4. 显式 NOT granted（注释作门外的文档约束，不再单独 REVOKE 覆盖默认）：
--    * 任何 pg_catalog 表（pg_authid / pg_shadow / pg_class …）—— 默认无 GRANT，
--      SELECT 必然 permission denied；
--    * pg_file_settings / pg_*_file / lo_* / dblink* / set_config / pg_sleep* 等
--      服务端函数——非 superuser 默认无 EXECUTE 权限；
--    * 信息架构视图（information_schema.*）—— 受其底层表权限约束，本角色也只能看到
--      自身被授权的 dim_/fact_ 表元信息；
--    * 任何写权限（INSERT/UPDATE/DELETE/TRUNCATE/CREATE/ALTER）—— 未授予。
--    * app / agent / memory / observability schema—— 未授予 USAGE。