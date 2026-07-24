# Intelligent Analysis Workbench HTML Prototype Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone interactive HTML prototype that demonstrates ReportAgent's approved intelligent analysis workbench without modifying the React application.

**Architecture:** Create one self-contained HTML file with semantic markup, scoped CSS variables, inline SVG charts, and vanilla JavaScript state transitions. Static scenario data will simulate backend-generated report blocks; user actions mutate the presentation as if the Agent had replanned the report.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, inline SVG

---

### Task 1: Build the workbench shell and visual system

**Files:**
- Create: `docs/intelligent-analysis-workbench.html`
- Reference: `docs/plans/2026-07-24-intelligent-analysis-workbench-design.md`

**Step 1: Create semantic page regions**

Add a fixed application shell with:

- top product bar
- left conversation rail
- central analysis canvas
- right analysis assistant
- toast/live region

Use buttons for every interactive control and provide visible focus states.

**Step 2: Define the design system**

Create CSS custom properties for navy structure colors, teal analysis accents, amber anomaly accents, paper surfaces, typography, radii, borders, and shadows.

Use a Chinese serif display stack for report headings and a Chinese sans-serif stack for controls and data. Add a subtle grid/paper texture and avoid generic equal-sized card grids.

**Step 3: Add responsive behavior**

At narrower widths, collapse the right assistant before the left rail. Preserve access through toolbar buttons. The report canvas remains readable down to tablet width.

**Step 4: Verify static structure**

Open `docs/intelligent-analysis-workbench.html` in a browser and confirm:

- no horizontal page overflow at 1440px
- central canvas scrolls independently
- left and right rails remain fixed
- keyboard focus is visible

**Step 5: Commit**

```bash
git add docs/intelligent-analysis-workbench.html
git commit -m "feat(docs): add intelligent analysis workbench shell"
```

### Task 2: Compose the dynamic report scenario

**Files:**
- Modify: `docs/intelligent-analysis-workbench.html`

**Step 1: Add the conversation context**

Render a concise user request and Agent interpretation above the report. Keep the conversation visually secondary once a report exists.

**Step 2: Add the report narrative**

Build a dynamic report containing:

1. report title, data range, source, and freshness
2. one-sentence executive finding
3. three to four KPI metrics with contextual changes
4. a primary monthly trend chart
5. a regional contribution comparison
6. anomaly and causal explanation callouts
7. expandable data-detail table
8. Agent-generated next-analysis recommendations

Use inline SVG for charts so the prototype remains dependency-free.

**Step 3: Show backend-driven structure**

In the right assistant, list the analysis modules selected by the Agent. Label the structure as generated from current data characteristics rather than as user-selected report formats.

**Step 4: Verify hierarchy**

Confirm the report can be understood in this order without opening the table: finding → KPI → trend/comparison → anomaly → recommended action.

**Step 5: Commit**

```bash
git add docs/intelligent-analysis-workbench.html
git commit -m "feat(docs): compose dynamic business analysis report"
```

### Task 3: Implement prototype interactions

**Files:**
- Modify: `docs/intelligent-analysis-workbench.html`

**Step 1: Add a small UI state model**

Store active conversation, focus mode, expanded table state, runtime state, applied adjustments, and toast timer in a single JavaScript object.

**Step 2: Implement conversation switching**

Clicking a conversation changes the active state and report header/summary. Keep non-primary scenarios lightweight but visibly distinct.

**Step 3: Implement focus mode**

The focus button hides both rails, widens the report, and changes its label to “退出聚焦”. Escape exits focus mode.

**Step 4: Implement Agent suggestions**

Suggestion buttons simulate requests such as:

- 增加华南区域对比
- 按月查看趋势
- 突出异常月份
- 查看产品贡献

Each click should:

1. append a compact user request to the conversation strip
2. mark the suggestion as applied
3. update at least one report title, legend, callout, or chart series
4. display a short “报告已更新” transition/toast

**Step 5: Implement free-text adjustment**

Submitting text such as “增加华南区域对比” triggers the matching adjustment. Unknown requests add a simulated Agent acknowledgement without breaking the report.

**Step 6: Implement secondary controls**

- expand/collapse all data rows
- expand/collapse Agent Runtime
- switch active conversation
- simulate export with a toast
- simulate save/favorite state

**Step 7: Verify interaction paths**

Manually test:

1. switch to another conversation and back
2. apply each suggestion once and ensure repeat clicks do not duplicate state
3. enter “增加华南区域对比” and see the comparison update
4. open and close runtime
5. expand and collapse table rows
6. enter focus mode and exit with Escape
7. activate export and observe feedback

**Step 8: Commit**

```bash
git add docs/intelligent-analysis-workbench.html
git commit -m "feat(docs): add workbench prototype interactions"
```

### Task 4: Browser verification and refinement

**Files:**
- Modify if needed: `docs/intelligent-analysis-workbench.html`

**Step 1: Run static validation**

Run:

```bash
node -e "const fs=require('fs'); const s=fs.readFileSync('docs/intelligent-analysis-workbench.html','utf8'); if(!s.includes('<!DOCTYPE html>')||!s.includes('<script>')||!s.includes('aria-live')) process.exit(1); console.log('HTML prototype checks passed')"
```

Expected: `HTML prototype checks passed`.

**Step 2: Open the prototype in Chrome**

Use the browser skill or open the local file directly. Verify at desktop and tablet viewport sizes.

**Step 3: Check browser console**

Expected: no JavaScript errors during all interaction paths.

**Step 4: Inspect visual states**

Capture or inspect:

- initial workbench
- report focus mode
- report after applying regional comparison
- expanded runtime
- expanded detail table

Refine clipping, contrast, density, and motion based on observed output.

**Step 5: Run repository checks**

Run:

```bash
git diff --check -- docs/intelligent-analysis-workbench.html docs/plans/2026-07-24-intelligent-analysis-workbench-design.md
git status --short
```

Expected: no whitespace errors; only intended documentation/prototype files are newly modified by this task.

**Step 6: Commit**

```bash
git add docs/intelligent-analysis-workbench.html docs/plans/2026-07-24-intelligent-analysis-workbench-design.md
git commit -m "docs: finalize intelligent analysis workbench prototype"
```
