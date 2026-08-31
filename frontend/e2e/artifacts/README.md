# Playwright E2E 测试产物归档

跑 `npm run e2e` / `npm run e2e:contract` / `npm run e2e:full` 后，**失败用例**的可调试产物落在本目录（成功用例不归档以省盘）。

## 目录布局

```
frontend/e2e/artifacts/
├── README.md                     # 本文件
├── test-results/                 # Playwright 自动产出（失败用例才有内容）
│   └── chromium/                 # project 名
│       └── <spec-name>/          # 例：01-happy-path、05-failed-result
│           ├── test-failed-1.png # 失败时刻的 PNG 截图
│           ├── test-failed-2.png # 重试时刻（如有 retries）
│           └── trace.zip         # 含视频 + 网络日志 + DOM snapshot + 截图序列
└── playwright-report/            # HTML 报告（如启用 reporter）
```

## 命名规则

| 文件 | 命名 | 含义 |
|---|---|---|
| `test-failed-N.png` | `test-failed-{N}.png` | 失败第 N 次的 PNG 截图（N=1；`retries=0` 时仅 1） |
| `trace.zip` | `<spec-name>/trace.zip` | Playwright trace，包含 video.webm + DOM 快照 + 网络日志 |
| `<spec>/test-failed-1.png` | 例：`01-happy-path/test-failed-1.png` | spec 编号-名/失败编号-截图 |

`spec-name` 来自 Playwright `testInfo.titlePath()` 的 spec 文件名（去 `.spec.ts`），例：`02-clarification`。

## 用法

### 失败后查看截图

```bash
# 1. 列所有失败用例的产物
ls frontend/e2e/artifacts/test-results/chromium/

# 2. 查看某次失败
xdg-open frontend/e2e/artifacts/test-results/chromium/05-failed-result/test-failed-1.png

# 3. trace.zip：解压或用 `npx playwright show-trace <path>` 看完整视频
npx playwright show-trace frontend/e2e/artifacts/test-results/chromium/05-failed-result/trace.zip
```

### 不进 git

`artifacts/` 由 `frontend/.gitignore` 排除（体积大且频繁变）。CI 上传作为构建 artifact。

## 配置位置

Playwright `outputDir: './e2e/artifacts'` 设于 `frontend/e2e/playwright.config.ts`。截图策略 `screenshot: 'only-on-failure'` + trace `retain-on-failure` ——只失败时才产出。

如果想强制每次成功也截图（review 用），可改 config：`screenshot: 'on'` 但会占更多盘。