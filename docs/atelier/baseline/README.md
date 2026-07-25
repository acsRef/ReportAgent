# 像素基线

`baseline.js` 脚本是 atelier demo 的视觉回归工具，可：

- 在多个 viewport（1440 / 1180 / 880）下截取 6 个场景到 `baseline/<viewport>/<name>.png`；
- 与基线对比，输出 `diff.png` 和失配比例。

**不依赖仓库其他环境**：脚本作为独立 Node 进程运行；只在具备 Playwright/Chromium 的机器上才能跑。

## 用法

```bash
# 安装依赖
cd docs/atelier/baseline
npm install
npx playwright install chromium

# 启动 demo 服务（在仓库根）
node scripts/dev-server.cjs docs 8765 &

# 截基线
node baseline.js capture

# 对比
node baseline.js check --threshold 0.005
```

## 场景清单

| Scene | Selector | 说明 |
| --- | --- | --- |
| 01-top    | `[data-atom="button"]`   | 顶栏 + 基础原子 |
| 02-form   | `[data-atom="text-field"]` | 表单 |
| 03-data   | `[data-atom="kpi"]`      | KPI 块 |
| 04-charts | `[data-atom="chart-pie"]` | 图表 |
| 05-report | `[data-atom="report-paper-full"]` | 完整 ReportPaper |
| 06-a11y   | `[data-atom="command-bar"]` | ⌘K 命令面板 |

## 阈值

`--threshold=0.001` 是 `pixelmatch` 的像素级颜色容差（HSV 距离），不是失配比例。脚本返回的 `diff` 是超过阈值的像素数。

推荐的 **失配比例容差**：

- 颜色 token 改动后：`0.02`（允许小范围色彩抖动）
- 结构性改动（增/删组件、改 layout）：`0.10`
- 字体替换或字号变化：`0.20`

## 失败处理

diff 图保存到 `baseline/<viewport>/<name>.diff.png`，可直接在图像查看器中对比 `name.png` 和 `diff.png` 找差异。

脚本通过 process.exit 码返回成功/失败，便于 CI 接入。
