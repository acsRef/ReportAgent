/* Gallery — fills each .atelier-card[data-atom] with a living sample. */
(function () {
  'use strict';

  function card(slug, title, body) {
    return `<div class="atelier-card__title">${title}</div><div class="atelier-card__body">${body}</div>`;
  }

  function sample(name, html) {
    const el = document.querySelector(`[data-atom="${name}"]`);
    if (!el) return;
    el.innerHTML = html;
  }

  // ============================ ATOM ============================
  sample('button', card('button', 'Button · 4 种变体 + 3 种尺寸 + loading', `
    <div class="atelier-demo-row">
      <button class="atelier-btn atelier-btn--primary">确认并生成</button>
      <button class="atelier-btn">次要操作</button>
      <button class="atelier-btn atelier-btn--quiet">文字按钮</button>
      <button class="atelier-btn atelier-btn--danger">删除</button>
    </div>
    <div class="atelier-demo-row" style="margin-top:8px">
      <button class="atelier-btn atelier-btn--primary atelier-btn--lg">大号主操作</button>
      <button class="atelier-btn atelier-btn--primary atelier-btn--sm">小号</button>
      <button class="atelier-btn atelier-btn--primary" disabled>禁用</button>
      <button class="atelier-btn atelier-btn--primary" aria-busy="true">加载中</button>
    </div>
  `));

  sample('icon-button', card('icon-button', 'IconButton · 强制 aria-label', `
    <div class="atelier-demo-row">
      <button class="atelier-icon-btn" aria-label="设置">⚙</button>
      <button class="atelier-icon-btn" aria-label="搜索">⌕</button>
      <button class="atelier-icon-btn" aria-label="关闭">×</button>
      <button class="atelier-icon-btn" aria-label="刷新" style="background:var(--teal);color:#fff">↻</button>
    </div>
  `));

  sample('tag', card('tag', 'Tag · 5 种 tone', `
    <div class="atelier-demo-row">
      <span class="atelier-tag">默认</span>
      <span class="atelier-tag atelier-tag--teal">已连接</span>
      <span class="atelier-tag atelier-tag--amber">需要补充</span>
      <span class="atelier-tag atelier-tag--red">生成失败</span>
      <span class="atelier-tag atelier-tag--green">已完成</span>
      <span class="atelier-tag atelier-tag--ink">已锁定</span>
    </div>
  `));

  sample('avatar', card('avatar', 'Avatar · 3 种尺寸', `
    <div class="atelier-demo-row">
      <span class="atelier-avatar" aria-label="管理员">管</span>
      <span class="atelier-avatar atelier-avatar--sm" style="background:var(--ink-2)" aria-label="用户">U</span>
      <span class="atelier-avatar atelier-avatar--lg" style="background:var(--amber)" aria-label="分析师">A</span>
    </div>
  `));

  sample('chip', card('chip', 'Chip · 选项胶囊', `
    <div class="atelier-demo-row">
      <button class="atelier-chip" data-chip="2024 全年">2024 全年</button>
      <button class="atelier-chip is-selected" data-chip="2025 全年">2025 全年</button>
      <button class="atelier-chip" data-chip="近 30 天">近 30 天</button>
    </div>
  `));

  sample('tooltip', card('tooltip', 'Tooltip · hover/focus 触发', `
    <div class="atelier-demo-row">
      <span class="atelier-btn" data-tooltip="这是 top 触发的提示" tabindex="0">悬停查看提示</span>
      <span class="atelier-btn" data-tooltip="右上方" tabindex="0" style="margin-left:6px">另一处提示</span>
    </div>
  `));

  sample('badge', card('badge', 'Badge · 数字徽标', `
    <div class="atelier-demo-row">
      <span class="atelier-icon-btn" aria-label="通知">🔔</span>
      <span class="atelier-badge" style="margin-left:-4px">3</span>
      <span class="atelier-icon-btn" aria-label="消息" style="margin-left:12px">✉</span>
      <span class="atelier-badge" style="margin-left:-4px">99+</span>
    </div>
  `));

  sample('divider', card('divider', 'Divider · 横线/竖线/带标签', `
    <div>上方区块</div>
    <hr class="atelier-divider"/>
    <div>下方区块</div>
    <div class="atelier-divider-label" style="margin-top:10px">带标签</div>
    <div class="atelier-demo-row" style="margin-top:6px"><span>左</span><span class="atelier-divider--vertical"></span><span>中</span><span class="atelier-divider--vertical"></span><span>右</span></div>
  `));

  // ============================ FORM ============================
  sample('text-field', card('text-field', 'TextField · 含错误态', `
    <div class="atelier-stack" style="max-width:360px">
      <label class="atelier-field">
        <span class="atelier-field__label">用户名</span>
        <input class="atelier-textfield" placeholder="请输入用户名" value="admin"/>
        <span class="atelier-field__hint">默认态</span>
      </label>
      <label class="atelier-field">
        <span class="atelier-field__label">邮箱（错误）</span>
        <input class="atelier-textfield is-error" value="not-an-email"/>
        <span class="atelier-field__error">邮箱格式不正确</span>
      </label>
      <label class="atelier-field">
        <span class="atelier-field__label">搜索框（带图标）</span>
        <span class="atelier-textfield-wrap">
          <input class="atelier-textfield" placeholder="搜索..." value=""/>
          <span class="atelier-textfield-wrap__icon" aria-hidden="true">⌕</span>
        </span>
      </label>
    </div>
  `));

  sample('text-area', card('text-area', 'TextArea · 可调整高度', `
    <div class="atelier-stack" style="max-width:360px">
      <label class="atelier-field">
        <span class="atelier-field__label">业务问题</span>
        <textarea class="atelier-textarea" rows="3" placeholder="例如：分析 2024 年华东销售趋势">分析 2024 年华东销售趋势，重点看异常月份。</textarea>
        <span class="atelier-field__hint">3 行起步</span>
      </label>
    </div>
  `));

  sample('checkbox', card('checkbox', 'Checkbox · 单 + 组', `
    <div class="atelier-checkbox-group">
      <label class="atelier-checkbox"><input type="checkbox" checked/> 月度趋势</label>
      <label class="atelier-checkbox"><input type="checkbox"/> 区域对比</label>
      <label class="atelier-checkbox"><input type="checkbox" checked/> 异常检测</label>
      <label class="atelier-checkbox"><input type="checkbox" disabled/> 商品下钻（未支持）</label>
    </div>
  `));

  sample('radio-group', card('radio-group', 'RadioGroup · 缺失信息选项', `
    <div class="atelier-radio-group" role="radiogroup" aria-label="时间范围">
      <label class="atelier-radio-pill"><input type="radio" name="t"/> 本月</label>
      <label class="atelier-radio-pill"><input type="radio" name="t" checked/> 2024 全年</label>
      <label class="atelier-radio-pill"><input type="radio" name="t"/> 上月</label>
      <label class="atelier-radio-pill"><input type="radio" name="t"/> 2025 全年</label>
    </div>
  `));

  sample('segmented', card('segmented', 'SegmentedControl · 模式切换', `
    <div class="atelier-segmented" role="tablist">
      <button class="atelier-segmented__item">① 平铺</button>
      <button class="atelier-segmented__item is-active">② 树形</button>
      <button class="atelier-segmented__item">③ 缩进</button>
      <button class="atelier-segmented__item">④ 交叉</button>
    </div>
  `));

  sample('select', card('select', 'Select · 原生下拉', `
    <div class="atelier-stack" style="max-width:280px">
      <label class="atelier-field"><span class="atelier-field__label">分析对象</span>
        <select class="atelier-select"><option>全部</option><option selected>销售</option><option>退货</option><option>库存</option></select>
      </label>
    </div>
  `));

  sample('slider', card('slider', 'Slider · 范围选择', `
    <div class="atelier-stack" style="max-width:360px">
      <div class="atelier-demo-row" style="justify-content:space-between"><span class="atelier-field__label">置信度阈值</span><b data-slider-output style="font-size:11px">0.65</b></div>
      <input class="atelier-slider" type="range" min="0" max="100" value="65" step="1" data-slider/>
    </div>
  `));

  sample('tag-input', card('tag-input', 'TagInput · 多标签输入', `
    <div class="atelier-stack" style="max-width:360px">
      <div class="atelier-tag-input">
        <span class="atelier-tag atelier-tag--teal">销售</span>
        <span class="atelier-tag atelier-tag--teal">库存</span>
        <span class="atelier-tag atelier-tag--teal">退货率</span>
        <input placeholder="按回车添加..."/>
      </div>
      <span class="atelier-field__hint">演示用 · 真实交互需要 JS 钩子</span>
    </div>
  `));

  sample('stepper', card('stepper', 'Stepper · 数字步进', `
    <div class="atelier-demo-row">
      <div class="atelier-stepper">
        <button class="atelier-stepper__btn" aria-label="减少">−</button>
        <input class="atelier-stepper__input" type="number" value="3" min="0" max="99"/>
        <button class="atelier-stepper__btn" aria-label="增加">+</button>
      </div>
      <div class="atelier-stepper">
        <button class="atelier-stepper__btn">−</button>
        <input class="atelier-stepper__input" value="42" disabled/>
        <button class="atelier-stepper__btn">+</button>
      </div>
    </div>
  `));

  sample('rate', card('rate', 'Rate · 评分', `
    <div class="atelier-rate" data-rate>
      <span class="atelier-rate__star is-on" data-rate-i="1">★</span>
      <span class="atelier-rate__star is-on" data-rate-i="2">★</span>
      <span class="atelier-rate__star is-on" data-rate-i="3">★</span>
      <span class="atelier-rate__star" data-rate-i="4">★</span>
      <span class="atelier-rate__star" data-rate-i="5">★</span>
    </div>
  `));

  sample('color', card('color', 'ColorInput · 颜色选择', `
    <div class="atelier-demo-row">
      <input class="atelier-color-input" type="color" value="#087f73"/>
      <input class="atelier-color-input" type="color" value="#b36c0d"/>
      <input class="atelier-color-input" type="color" value="#b94a48"/>
      <input class="atelier-color-input" type="color" value="#293d53"/>
    </div>
  `));

  sample('date-time', card('date-time', 'Date / Time Input', `
    <div class="atelier-demo-row" style="flex-wrap:wrap">
      <label class="atelier-date-input"><span>📅</span><input type="date" value="2024-07-24"/></label>
      <label class="atelier-date-input"><span>🗓</span><input type="text" placeholder="选择日期范围"/></label>
      <label class="atelier-time-input"><span>⏱</span><input type="time" value="14:30"/></label>
      <label class="atelier-time-input"><span>⌚</span><input type="datetime-local" value="2024-07-24T14:30"/></label>
    </div>
  `));

  sample('switch', card('switch', 'Switch · 开关', `
    <div class="atelier-stack">
      <label class="atelier-switch-row"><span>启用自动保存</span><span class="atelier-switch"><input type="checkbox" checked/><span class="atelier-switch__track"></span></span></label>
      <label class="atelier-switch-row"><span>深色模式</span><span class="atelier-switch"><input type="checkbox"/><span class="atelier-switch__track"></span></span></label>
      <label class="atelier-switch-row"><span>仅看摘要</span><span class="atelier-switch"><input type="checkbox" checked/><span class="atelier-switch__track"></span></span></label>
    </div>
  `));

  sample('upload', card('upload', 'Upload · 单/多/头像', `
    <div class="atelier-demo-row">
      <label class="atelier-upload" style="width:200px">
        <span class="atelier-upload__icon">⤴</span>
        <span>点击或拖拽文件到此处</span>
        <span style="font-size:8px">支持 CSV / Excel / JSON（&lt; 5MB）</span>
        <input type="file" style="display:none"/>
      </label>
      <label class="atelier-upload" style="width:160px"><span class="atelier-upload__icon">📄</span><span>附件 3 个</span><input type="file" multiple style="display:none"/></label>
      <label class="atelier-upload atelier-upload--avatar">+<input type="file" accept="image/*" style="display:none"/></label>
    </div>
  `));

  sample('cascader', card('cascader', 'Cascader · 多级联动', `
    <div class="atelier-cascader">
      <select class="atelier-cascader__seg" aria-label="区域"><option>华东</option><option>华南</option><option>华北</option></select>
      <select class="atelier-cascader__seg" aria-label="城市"><option>上海</option><option>杭州</option><option>南京</option></select>
      <select class="atelier-cascader__seg" aria-label="门店"><option>徐家汇店</option><option>陆家嘴店</option><option>西湖店</option></select>
    </div>
  `));

  sample('transfer', card('transfer', 'Transfer · 穿梭框', `
    <div class="atelier-transfer">
      <div class="atelier-transfer__pane">
        <div class="atelier-transfer__title">可选维度</div>
        <label class="atelier-transfer__row"><input type="checkbox"/> 月份</label>
        <label class="atelier-transfer__row"><input type="checkbox"/> 城市</label>
        <label class="atelier-transfer__row"><input type="checkbox"/> 产品品类</label>
        <label class="atelier-transfer__row"><input type="checkbox"/> 渠道</label>
        <label class="atelier-transfer__row"><input type="checkbox"/> 客户等级</label>
      </div>
      <div class="atelier-transfer__ops">
        <button class="atelier-transfer__op" aria-label="全部加入">⏵⏵</button>
        <button class="atelier-transfer__op" aria-label="加入选中">⏵</button>
        <button class="atelier-transfer__op" aria-label="移除选中">⏴</button>
        <button class="atelier-transfer__op" aria-label="全部移除">⏴⏴</button>
      </div>
      <div class="atelier-transfer__pane">
        <div class="atelier-transfer__title">已选维度</div>
        <label class="atelier-transfer__row"><input type="checkbox" checked/> 月份</label>
        <label class="atelier-transfer__row"><input type="checkbox" checked/> 产品品类</label>
      </div>
    </div>
  `));

  sample('tree-select', card('tree-select', 'TreeSelect · 树形选择', `
    <div class="atelier-tree-select" style="max-width:280px">
      <div class="atelier-tree-select__node" data-depth="1">▾ 销售</div>
      <div class="atelier-tree-select__node" data-depth="2">线上销售</div>
      <div class="atelier-tree-select__node" data-depth="3">线上直销</div>
      <div class="atelier-tree-select__node" data-depth="3">线上经销</div>
      <div class="atelier-tree-select__node" data-depth="2">线下销售</div>
      <div class="atelier-tree-select__node" data-depth="3">门店销售</div>
      <div class="atelier-tree-select__node" data-depth="3">批发</div>
      <div class="atelier-tree-select__node" data-depth="1">▸ 库存</div>
    </div>
  `));

  sample('autocomplete', card('autocomplete', 'Autocomplete · 自动完成', `
    <div class="atelier-autocomplete" style="max-width:360px">
      <input class="atelier-textfield" placeholder="搜索指标或表名" value="销售"/>
      <div class="atelier-autocomplete__menu">
        <div class="atelier-autocomplete__item is-active">fact_sales <small>table</small></div>
        <div class="atelier-autocomplete__item">销售额 <small>metric</small></div>
        <div class="atelier-autocomplete__item">销售毛利 <small>metric</small></div>
        <div class="atelier-autocomplete__item">SalesChannel 维度 <small>dim</small></div>
      </div>
    </div>
  `));

  sample('multi-select', card('multi-select', 'MultiSelect · 列表多选', `
    <div class="atelier-multi-select" style="max-width:280px">
      <label class="atelier-multi-select__row"><input type="checkbox" checked/> 销售额<small>万元</small></label>
      <label class="atelier-multi-select__row"><input type="checkbox" checked/> 毛利额<small>万元</small></label>
      <label class="atelier-multi-select__row"><input type="checkbox"/> 客单价<small>元</small></label>
      <label class="atelier-multi-select__row"><input type="checkbox" checked/> 毛利率<small>%</small></label>
      <label class="atelier-multi-select__row"><input type="checkbox"/> 退货率<small>%</small></label>
    </div>
  `));

  // ============================ STATUS & LAYOUT ============================
  sample('spinner', card('spinner', 'Spinner · 3 尺寸', `
    <div class="atelier-demo-row">
      <span class="atelier-spinner atelier-spinner--sm" aria-label="加载中"></span>
      <span class="atelier-spinner" aria-label="加载中"></span>
      <span class="atelier-spinner atelier-spinner--lg" aria-label="加载中"></span>
      <span class="atelier-spinner-label">正在解析需求…</span>
    </div>
  `));

  sample('skeleton', card('skeleton', 'Skeleton · 骨架占位', `
    <div class="atelier-stack" style="max-width:320px">
      <div class="atelier-skeleton" style="height:14px; width:60%"></div>
      <div class="atelier-skeleton" style="height:10px; width:100%"></div>
      <div class="atelier-skeleton" style="height:10px; width:80%"></div>
      <div class="atelier-skeleton" style="height:60px; width:100%"></div>
    </div>
  `));

  sample('empty', card('empty', 'Empty · 空态', `
    <div class="atelier-empty">
      <div class="atelier-empty__icon">○</div>
      <div class="atelier-empty__title">暂无历史报告</div>
      <div class="atelier-empty__desc">完成一次分析后，报告会自动归档到这里。</div>
    </div>
  `));

  sample('progress', card('progress', 'Progress · 4 阶段进度条', `
    <div class="atelier-stack" style="max-width:420px">
      <div class="atelier-progress" data-progress="62"><div class="atelier-progress__fill"></div></div>
      <div class="atelier-progress is-striped" data-progress="35"><div class="atelier-progress__fill"></div></div>
    </div>
  `));

  sample('card', card('card', 'Card / Paper / Panel', `
    <div class="atelier-demo-row">
      <div class="atelier-card-inner" style="width:180px">
        <b style="font:700 11px var(--font-display)">Card</b>
        <span class="atelier-field__hint">白底 + 细线</span>
      </div>
      <div class="atelier-card-inner atelier-card-inner--paper" style="width:180px">
        <b style="font:700 11px var(--font-display)">Paper</b>
        <span class="atelier-field__hint">纸张白 + 阴影</span>
      </div>
      <div class="atelier-panel" style="width:200px">
        <div class="atelier-panel__head">
          <span class="atelier-panel__title">Panel</span>
          <span class="atelier-panel__sub">带 head/sub</span>
        </div>
        <div class="atelier-panel__body" style="color:var(--muted);font-size:10px">常用于报告内的子面板</div>
      </div>
    </div>
  `));

  sample('panel', card('panel', 'Panel · 实际用例（取自报告 trend/composition）', `
    <div class="atelier-grid-2" style="grid-template-columns:1.55fr 1fr">
      <div class="atelier-panel">
        <div class="atelier-panel__head">
          <div>
            <div class="atelier-panel__title">月度销售趋势</div>
            <div class="atelier-panel__sub">单位：万元</div>
          </div>
        </div>
        <div class="atelier-panel__body">
          <div class="atelier-chart" style="height:140px;padding:8px"><svg viewBox="0 0 360 110" preserveAspectRatio="none">
            <g stroke="#e6eae6"><line x1="20" y1="10" x2="340" y2="10"/><line x1="20" y1="40" x2="340" y2="40"/><line x1="20" y1="70" x2="340" y2="70"/><line x1="20" y1="100" x2="340" y2="100"/></g>
            <path d="M25 90 L60 80 L95 72 L130 60 L165 50 L200 38 L235 30 L270 22 L305 60 L340 25" fill="none" stroke="#087f73" stroke-width="2.4" stroke-linecap="round"/>
            <g fill="#087f73"><circle cx="25" cy="90" r="2.4"/><circle cx="130" cy="60" r="2.4"/><circle cx="235" cy="30" r="2.4"/><circle cx="305" cy="60" r="3.2" fill="#b36c0d"/></g>
          </svg></div>
        </div>
      </div>
      <div class="atelier-panel">
        <div class="atelier-panel__head">
          <div>
            <div class="atelier-panel__title">品类贡献</div>
            <div class="atelier-panel__sub">销售额占比</div>
          </div>
        </div>
        <div class="atelier-panel__body">
          <div style="position:relative;width:90px;height:90px;border-radius:50%;background:conic-gradient(var(--teal) 0 44%,#5aa99e 44% 71%,#b9d4ce 71% 88%,#dce6e2 88%);margin:6px auto 8px">
            <div style="position:absolute;inset:14px;border-radius:50%;background:#fff"></div>
            <div style="position:absolute;inset:0;display:grid;place-items:center;font:700 14px var(--font-display)">44%</div>
          </div>
          <div class="atelier-driver-list" style="font-size:9px">
            <div class="atelier-driver" style="grid-template-columns:1fr 60px"><span>电子产品</span><span style="text-align:right;font-weight:700">14.4 万</span></div>
            <div class="atelier-driver" style="grid-template-columns:1fr 60px"><span>服装鞋帽</span><span style="text-align:right;font-weight:700">8.8 万</span></div>
            <div class="atelier-driver" style="grid-template-columns:1fr 60px"><span>家电</span><span style="text-align:right;font-weight:700">5.6 万</span></div>
          </div>
        </div>
      </div>
    </div>
  `));

  sample('paper', card('paper', 'Paper · 报告纸张', `
    <div class="atelier-paper">
      <div class="atelier-report__kicker">REPORT / v1</div>
      <div class="atelier-report__title">华东区域销售经营分析</div>
      <div class="atelier-report__meta">
        <span><i>数据范围</i>2024.01 — 2024.12</span>
        <span><i>分析范围</i>华东 · 全品类</span>
      </div>
      <div class="atelier-report__finding">
        <div class="atelier-report__finding-label">核心发现</div>
        <div class="atelier-report__finding-text">华东全年销售额保持增长，但 <strong>8 月出现阶段性回落</strong>。</div>
      </div>
    </div>
  `));

  sample('stack', card('stack', 'Stack · 垂直间距', `
    <div class="atelier-stack" style="max-width:280px">
      <div class="atelier-card-inner">第一项</div>
      <div class="atelier-card-inner">第二项</div>
      <div class="atelier-card-inner">第三项</div>
      <div class="atelier-card-inner">第四项</div>
    </div>
  `));

  sample('grid', card('grid', 'Grid · 3/4 列响应式', `
    <div class="atelier-grid-3" style="margin-bottom:8px">
      <div class="atelier-card-inner">A</div><div class="atelier-card-inner">B</div><div class="atelier-card-inner">C</div>
    </div>
    <div class="atelier-grid-4">
      <div class="atelier-card-inner">1</div><div class="atelier-card-inner">2</div><div class="atelier-card-inner">3</div><div class="atelier-card-inner">4</div>
    </div>
  `));

  sample('section', card('section', 'Section · 报告区段', `
    <div class="atelier-card-inner" style="padding:0">
      <div class="atelier-section-card">
        <div class="atelier-section-card__index">01 / SCALE</div>
        <div class="atelier-section-card__title">经营规模与效率</div>
      </div>
      <div class="atelier-section-card">
        <div class="atelier-section-card__index">02 / MOMENTUM</div>
        <div class="atelier-section-card__title">增长节奏与贡献结构</div>
      </div>
    </div>
  `));

  // ============================ FEEDBACK ============================
  sample('toast', card('toast', 'Toast · 顶部居中', `
    <div class="atelier-demo-row">
      <button class="atelier-btn atelier-btn--primary" data-toast="success">成功</button>
      <button class="atelier-btn atelier-btn--quiet" data-toast="info">信息</button>
      <button class="atelier-btn" data-toast="warning">警告</button>
      <button class="atelier-btn atelier-btn--danger" data-toast="error">错误</button>
    </div>
  `));

  sample('notification', card('notification', 'Notification · 右上角', `
    <div class="atelier-demo-row">
      <button class="atelier-btn" data-notif="success">成功通知</button>
      <button class="atelier-btn" data-notif="warning">带副标题</button>
    </div>
  `));

  sample('modal', card('modal', 'Modal · 居中弹窗', `
    <div class="atelier-demo-row">
      <button class="atelier-btn atelier-btn--primary" data-modal>打开 Modal</button>
    </div>
  `));

  sample('drawer', card('drawer', 'Drawer · 右侧抽屉', `
    <div class="atelier-demo-row">
      <button class="atelier-btn" data-drawer>打开 Drawer</button>
    </div>
  `));

  sample('popconfirm', card('popconfirm', 'Popconfirm · 轻量确认', `
    <div class="atelier-demo-row">
      <button class="atelier-btn atelier-btn--danger" data-popconfirm>删除模板</button>
    </div>
  `));

  sample('dropdown', card('dropdown', 'Dropdown · 下拉菜单', `
    <div class="atelier-demo-row">
      <button class="atelier-btn" data-dropdown>操作 ▾</button>
    </div>
  `));

  sample('contextmenu', card('contextmenu', 'ContextMenu · 右键', `
    <div class="atelier-card-inner" data-contextmenu style="cursor:context-menu;min-height:80px;display:grid;place-items:center;color:var(--muted)">
      在此处右键打开菜单
    </div>
  `));

  // ============================ DATA ============================
  sample('kpi', card('kpi', 'KpiBlock · 3 / 4 / 5 列 + Hero 变体', `
    <div class="atelier-kpi-grid atelier-kpi-grid--4" style="border-color:var(--line-2)">
      <div class="atelier-kpi"><div class="atelier-kpi__label">全年销售额</div><div class="atelier-kpi__value">32.8<span class="atelier-kpi__unit">万</span></div><div class="atelier-kpi__delta">↑ 18.6%</div><div class="atelier-kpi__bar"><span style="width:78%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">毛利</div><div class="atelier-kpi__value">10.4<span class="atelier-kpi__unit">万</span></div><div class="atelier-kpi__delta">↑ 14.2%</div><div class="atelier-kpi__bar"><span style="width:69%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">客单价</div><div class="atelier-kpi__value">6,842<span class="atelier-kpi__unit">元</span></div><div class="atelier-kpi__delta is-down">↓ 3.8%</div><div class="atelier-kpi__bar"><span style="width:55%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">毛利率</div><div class="atelier-kpi__value">31.7<span class="atelier-kpi__unit">%</span></div><div class="atelier-kpi__delta">↑ 1.4pp</div><div class="atelier-kpi__bar"><span style="width:82%"></span></div></div>
    </div>
    <div class="atelier-kpi-grid atelier-kpi-grid--5" style="margin-top:8px">
      <div class="atelier-kpi"><div class="atelier-kpi__label">DAU</div><div class="atelier-kpi__value">12.3k</div><div class="atelier-kpi__delta">↑ 4.1%</div><div class="atelier-kpi__bar"><span style="width:64%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">转化率</div><div class="atelier-kpi__value">4.8<span class="atelier-kpi__unit">%</span></div><div class="atelier-kpi__delta">↑ 0.3pp</div><div class="atelier-kpi__bar"><span style="width:48%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">客单价</div><div class="atelier-kpi__value">¥186</div><div class="atelier-kpi__delta is-down">↓ 1.2%</div><div class="atelier-kpi__bar"><span style="width:42%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">复购率</div><div class="atelier-kpi__value">38<span class="atelier-kpi__unit">%</span></div><div class="atelier-kpi__delta">↑ 2.1pp</div><div class="atelier-kpi__bar"><span style="width:78%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">投诉</div><div class="atelier-kpi__value">23</div><div class="atelier-kpi__delta is-down">↓ 18%</div><div class="atelier-kpi__bar"><span style="width:30%"></span></div></div>
    </div>
    <div class="atelier-kpi-grid atelier-kpi-grid--3" style="margin-top:8px">
      <div class="atelier-kpi atelier-kpi--hero"><div class="atelier-kpi__label">报告核心 · Hero</div><div class="atelier-kpi__value">¥ 3.2M</div><div class="atelier-kpi__delta">↑ 22%</div><div class="atelier-kpi__bar"><span style="width:88%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">华东贡献</div><div class="atelier-kpi__value">44<span class="atelier-kpi__unit">%</span></div><div class="atelier-kpi__delta">↑ 2pp</div><div class="atelier-kpi__bar"><span style="width:44%"></span></div></div>
      <div class="atelier-kpi"><div class="atelier-kpi__label">异常月份</div><div class="atelier-kpi__value">1</div><div class="atelier-kpi__delta is-down">↓ 1</div><div class="atelier-kpi__bar"><span style="width:15%"></span></div></div>
    </div>
  `));

  sample('kpi-spark', card('kpi-spark', 'Kpi + Sparkline · 紧凑趋势', `
    <div class="atelier-kpi-grid atelier-kpi-grid--3" style="border-color:var(--line-2)">
      <div class="atelier-kpi">
        <div class="atelier-kpi__label">周活</div>
        <div class="atelier-kpi__value" style="display:flex;align-items:baseline;gap:6px">28.4k <span class="atelier-sparkline atelier-sparkline--up"><svg viewBox="0 0 80 22"><path d="M2 18 L12 14 L22 16 L32 10 L42 12 L52 6 L62 8 L72 4 L78 3"/></svg></span></div>
        <div class="atelier-kpi__delta">↑ 6.2%</div>
      </div>
      <div class="atelier-kpi">
        <div class="atelier-kpi__label">留存</div>
        <div class="atelier-kpi__value" style="display:flex;align-items:baseline;gap:6px">62% <span class="atelier-sparkline atelier-sparkline--down"><svg viewBox="0 0 80 22"><path d="M2 4 L12 6 L22 5 L32 9 L42 7 L52 11 L62 12 L72 16 L78 18"/></svg></span></div>
        <div class="atelier-kpi__delta is-down">↓ 1.1pp</div>
      </div>
      <div class="atelier-kpi">
        <div class="atelier-kpi__label">新访</div>
        <div class="atelier-kpi__value" style="display:flex;align-items:baseline;gap:6px">3.8k <span class="atelier-sparkline atelier-sparkline--flat"><svg viewBox="0 0 80 22"><path d="M2 10 L12 11 L22 10 L32 11 L42 10 L52 10 L62 11 L72 10 L78 10"/></svg></span></div>
        <div class="atelier-kpi__delta">± 0.0%</div>
      </div>
    </div>
  `));

  /* 通用图表小工具 */
  function axis(svg, w, h) {
    return `<g class="atelier-axis"><line x1="40" y1="${h-22}" x2="${w-20}" y2="${h-22}"/><line x1="40" y1="14" x2="40" y2="${h-22}"/></g>`;
  }
  function yLabels(values, h) {
    return values.map((v, i) => `<text class="atelier-axis-label" x="32" y="${(h-22) - (i*(h-50)/3)}" text-anchor="end">${v}</text>`).join('');
  }
  function xLabels(values, w, h) {
    return values.map((v, i) => `<text class="atelier-axis-label" x="${40 + (i*(w-60)/(values.length-1))}" y="${h-6}" text-anchor="middle">${v}</text>`).join('');
  }

  sample('chart-bars', card('chart-bars', '垂直柱状图 · 多系列', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">各品类销售额对比</div><div class="atelier-chart__sub">2024 全年 · 单位：万元</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>本年</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b9d4ce"></i>对比</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','10','20','30','40'], 200)}
          <g>
            <rect class="atelier-bars-bar" x="60" y="80" width="40" height="98"/>
            <rect class="atelier-bars-bar" x="160" y="100" width="40" height="78" style="fill:#5aa99e"/>
            <rect class="atelier-bars-bar" x="260" y="120" width="40" height="58"/>
            <rect class="atelier-bars-bar" x="360" y="60" width="40" height="118"/>
            <rect class="atelier-bars-bar" x="460" y="40" width="40" height="138"/>
          </g>
          <g style="fill:#b9d4ce">
            <rect x="100" y="120" width="40" height="58"/>
            <rect x="200" y="130" width="40" height="48"/>
            <rect x="300" y="140" width="40" height="38"/>
            <rect x="400" y="110" width="40" height="68"/>
            <rect x="500" y="100" width="40" height="78"/>
          </g>
          ${xLabels(['电子','服装','家电','食饮','其他'], 600, 200)}
        </svg>
      </div>
    </div>
  `));

  sample('chart-bars-horizontal', card('chart-bars-horizontal', '水平柱状图 · Top N', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">城市销售额 Top 6</div><div class="atelier-chart__sub">降序</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g class="atelier-axis"><line x1="120" y1="20" x2="120" y2="180"/></g>
          ${[['上海',142,18],['杭州',120,38],['广州',110,28],['南京',92,40],['苏州',76,30],['宁波',60,38]].map((c, i) => {
            const y = 24 + i*26;
            return `<text class="atelier-axis-label" x="112" y="${y+10}" text-anchor="end">${c[0]}</text>
              <rect class="atelier-bars-bar" x="124" y="${y}" width="${c[1]*3}" height="14"/>
              <text class="atelier-axis-label" x="${130+c[1]*3}" y="${y+10}">${c[1]}</text>`;
          }).join('')}
        </svg>
      </div>
    </div>
  `));

  sample('chart-line', card('chart-line', '折线图 · 双系列 + 异常高亮', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">月度销售趋势</div><div class="atelier-chart__sub">华东 vs 华南</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>华东</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b36c0d"></i>华南</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','10','20','30','40'], 200)}
          <path class="atelier-line" d="M50 130 L100 118 L150 100 L200 90 L250 76 L300 64 L350 50 L400 40 L450 92 L500 60 L550 38"/>
          <path class="atelier-line atelier-line--amber" d="M50 142 L100 132 L150 124 L200 116 L250 108 L300 96 L350 88 L400 80 L450 92 L500 84 L550 78"/>
          <circle cx="450" cy="92" r="5" class="atelier-point--highlight"/>
          ${xLabels(['1','2','3','4','5','6','7','8','9','10','11','12'], 600, 200)}
        </svg>
      </div>
    </div>
  `));

  sample('chart-area', card('chart-area', '面积图 · 堆叠', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">渠道贡献（堆叠面积）</div><div class="atelier-chart__sub">12 个月累计</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>线上</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#5aa99e"></i>门店</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b9d4ce"></i>批发</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','20','40','60','80'], 200)}
          <path class="atelier-area" d="M50 160 L100 150 L150 140 L200 130 L250 120 L300 110 L350 100 L400 90 L450 80 L500 70 L550 60 L580 55 L580 175 L50 175 Z"/>
          <path fill="rgba(8,127,115,0.28)" d="M50 130 L100 122 L150 114 L200 108 L250 100 L300 92 L350 86 L400 78 L450 68 L500 60 L550 50 L580 46 L580 55 L550 60 L500 70 L450 80 L400 90 L350 100 L300 110 L250 120 L200 130 L150 140 L100 150 L50 160 Z"/>
          <path fill="rgba(8,127,115,0.55)" d="M50 110 L100 105 L150 95 L200 90 L250 82 L300 76 L350 70 L400 62 L450 55 L500 48 L550 40 L580 36 L580 46 L550 50 L500 60 L450 68 L400 78 L350 86 L300 92 L250 100 L200 108 L150 114 L100 122 L50 130 Z"/>
        </svg>
      </div>
    </div>
  `));

  sample('chart-stack', card('chart-stack', '堆叠柱状图 · 占比', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">品类月度销售构成</div><div class="atelier-chart__sub">4 个月对比</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>电子</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#5aa99e"></i>服装</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b9d4ce"></i>其他</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','20','40','60','80'], 200)}
          ${[['1月',70,30,40],['2月',60,30,40],['3月',50,30,40],['4月',40,30,40]].map((m, i) => {
            const x = 80 + i*120; const h1 = m[1]*1.6, h2 = m[2]*1.6, h3 = m[3]*1.6; const y0 = 178;
            return `<rect class="atelier-bars-bar" x="${x}" y="${y0-h1-h2-h3}" width="50" height="${h1+h2+h3}"/>
              <rect fill="#5aa99e" x="${x}" y="${y0-h2-h3}" width="50" height="${h2+h3}"/>
              <rect fill="#b9d4ce" x="${x}" y="${y0-h3}" width="50" height="${h3}"/>
              <text class="atelier-axis-label" x="${x+25}" y="${y0+12}" text-anchor="middle">${m[0]}</text>`;
          }).join('')}
        </svg>
      </div>
    </div>
  `));

  sample('chart-dual', card('chart-dual', '双轴对比 · 柱+线', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">销售额 vs 毛利率</div><div class="atelier-chart__sub">左轴：销售额（万元） · 右轴：毛利率（%）</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>销售额</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b36c0d"></i>毛利率</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','10','20','30','40'], 200)}
          <text class="atelier-axis-label" x="595" y="20" text-anchor="end">50%</text>
          <text class="atelier-axis-label" x="595" y="175" text-anchor="end">0%</text>
          <g>
            <rect class="atelier-bars-bar" x="60" y="100" width="40" height="78"/>
            <rect class="atelier-bars-bar" x="160" y="80" width="40" height="98"/>
            <rect class="atelier-bars-bar" x="260" y="60" width="40" height="118"/>
            <rect class="atelier-bars-bar" x="360" y="40" width="40" height="138"/>
            <rect class="atelier-bars-bar" x="460" y="50" width="40" height="128"/>
          </g>
          <path class="atelier-line atelier-line--amber" d="M80 110 L180 95 L280 75 L380 55 L480 65" style="fill:none"/>
          <g fill="#b36c0d" stroke="#fff" stroke-width="1.5">
            <circle cx="80" cy="110" r="3"/><circle cx="180" cy="95" r="3"/><circle cx="280" cy="75" r="3"/><circle cx="380" cy="55" r="3"/><circle cx="480" cy="65" r="3"/>
          </g>
          ${xLabels(['上海','杭州','南京','苏州','宁波'], 600, 200)}
        </svg>
      </div>
    </div>
  `));

  /* 饼图/玫瑰图通用算法 */
  function pieGeometry(values, r) {
    const total = values.reduce((a, b) => a + b, 0);
    let acc = 0;
    return values.map((v) => {
      const start = (acc / total) * Math.PI * 2 - Math.PI / 2;
      acc += v;
      const end = (acc / total) * Math.PI * 2 - Math.PI / 2;
      return { start, end, mid: (start + end) / 2, value: v, ratio: v / total };
    });
  }
  function arcPath(cx, cy, r, start, end, largeArc) {
    const sx = cx + Math.cos(start) * r;
    const sy = cy + Math.sin(start) * r;
    const ex = cx + Math.cos(end) * r;
    const ey = cy + Math.sin(end) * r;
    return `M ${cx} ${cy} L ${sx} ${sy} A ${r} ${r} 0 ${largeArc} 1 ${ex} ${ey} Z`;
  }
  function donutSegment(cx, cy, rOuter, rInner, start, end) {
    const sx1 = cx + Math.cos(start) * rOuter, sy1 = cy + Math.sin(start) * rOuter;
    const sx2 = cx + Math.cos(start) * rInner, sy2 = cy + Math.sin(start) * rInner;
    const ex1 = cx + Math.cos(end) * rOuter, ey1 = cy + Math.sin(end) * rOuter;
    const ex2 = cx + Math.cos(end) * rInner, ey2 = cy + Math.sin(end) * rInner;
    const large = end - start > Math.PI ? 1 : 0;
    return `M ${sx1} ${sy1} A ${rOuter} ${rOuter} 0 ${large} 1 ${ex1} ${ey1} L ${ex2} ${ey2} A ${rInner} ${rInner} 0 ${large} 0 ${sx2} ${sy2} Z`;
  }

  sample('chart-pie', card('chart-pie', '饼图 · 强调块', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">品类销售占比</div><div class="atelier-chart__sub">强调：电子产品</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(100 100)">
            ${(() => {
              const data = [44, 27, 17, 12];
              const colors = ['#087f73', '#5aa99e', '#b9d4ce', '#dce6e2'];
              const segments = pieGeometry(data, 64);
              return segments.map((seg, i) => {
                const large = seg.end - seg.start > Math.PI ? 1 : 0;
                const offset = i === 0 ? 6 : 0;
                const ox = Math.cos(seg.mid) * offset;
                const oy = Math.sin(seg.mid) * offset;
                return `<path d="${arcPath(ox, oy, 60, seg.start, seg.end, large)}" fill="${colors[i]}" stroke="#fff" stroke-width="1.5"/>`;
              }).join('');
            })()}
            <text text-anchor="middle" y="0" font-size="15" font-weight="700" fill="#10243e">44%</text>
            <text text-anchor="middle" y="13" font-size="8" fill="#68798a">电子产品</text>
          </g>
          <g transform="translate(240 36)">
            ${['电子产品 44%','服装鞋帽 27%','家电 17%','其他 12%'].map((t, i) => `<g transform="translate(0 ${i*30})">
              <rect width="10" height="10" y="0" fill="${['#087f73','#5aa99e','#b9d4ce','#dce6e2'][i]}"/>
              <text class="atelier-axis-label" x="16" y="9">${t}</text>
            </g>`).join('')}
          </g>
        </svg>
      </div>
    </div>
  `));

  sample('chart-donut', card('chart-donut', '环形图 · 中心数值', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">订单来源</div><div class="atelier-chart__sub">总订单 1,284</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(100 100)">
            ${(() => {
              const data = [44, 27, 17, 12];
              const colors = ['#087f73', '#5aa99e', '#b9d4ce', '#dce6e2'];
              const segments = pieGeometry(data, 60);
              return segments.map((seg, i) => `<path d="${donutSegment(0, 0, 60, 40, seg.start, seg.end)}" fill="${colors[i]}" stroke="#fff" stroke-width="1.5"/>`).join('');
            })()}
            <text text-anchor="middle" y="-2" font-size="18" font-weight="700" fill="#10243e">44%</text>
            <text text-anchor="middle" y="12" font-size="9" fill="#68798a">APP 端</text>
          </g>
          <g transform="translate(240 36)">
            ${[['APP 44%','#087f73'],['Web 27%','#5aa99e'],['小程序 17%','#b9d4ce'],['其他 12%','#dce6e2']].map((t, i) => `<g transform="translate(0 ${i*30})">
              <rect width="10" height="10" fill="${t[1]}"/>
              <text class="atelier-axis-label" x="16" y="9">${t[0]}</text>
            </g>`).join('')}
          </g>
        </svg>
      </div>
    </div>
  `));

  /* 玫瑰图：等角扇形 + 不同半径 */
  sample('chart-rose', card('chart-rose', '玫瑰图 · 等角不等径', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">订单时段分布</div><div class="atelier-chart__sub">24h · 等角 15°，半径 = 订单量</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(100 100)">
            <circle r="60" fill="none" stroke="var(--line)" stroke-dasharray="2 3"/>
            <circle r="40" fill="none" stroke="var(--line)" stroke-dasharray="2 3"/>
            <circle r="20" fill="none" stroke="var(--line)" stroke-dasharray="2 3"/>
            ${(() => {
              const segs = pieGeometry([12, 18, 26, 34, 28, 20, 14, 8, 6, 10, 16, 22], 56);
              return segs.map((seg, i) => {
                const r = 8 + seg.ratio * 52;
                const large = seg.end - seg.start > Math.PI ? 1 : 0;
                return `<path d="${arcPath(0, 0, r, seg.start, seg.end, large)}" fill="#087f73" fill-opacity="${0.4 + i * 0.05}" stroke="#fff" stroke-width="1"/>`;
              }).join('');
            })()}
          </g>
        </svg>
      </div>
    </div>
  `));

  /* 仪表盘：半圆 + 指针 */
  sample('chart-gauge', card('chart-gauge', 'Gauge · 仪表盘', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">订单达成率</div><div class="atelier-chart__sub">月度目标 100 万元</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(300 160)">
            <path d="M -120 0 A 120 120 0 0 1 120 0" fill="none" stroke="var(--line)" stroke-width="14" stroke-linecap="round"/>
            <path d="M -120 0 A 120 120 0 0 1 ${(function(){const a=Math.PI - 0.78; return 120*Math.cos(a);})()} ${(function(){const a=Math.PI - 0.78; return 120*Math.sin(a);})()}" fill="none" stroke="var(--teal)" stroke-width="14" stroke-linecap="round"/>
            <g transform="rotate(35)"><line x1="0" y1="0" x2="0" y2="-95" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/><circle r="6" fill="var(--ink)"/></g>
            <text text-anchor="middle" y="20" font-size="22" font-weight="700" fill="var(--ink)">72%</text>
            <text text-anchor="middle" y="38" font-size="9" fill="var(--muted)">已达成 ¥720K</text>
          </g>
        </svg>
      </div>
    </div>
  `));

  /* 雷达图：5 维 */
  sample('chart-radar', card('chart-radar', 'Radar · 5 维评估', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">五维能力评估</div><div class="atelier-chart__sub">当前 vs 目标</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(300 100)">
            ${(() => {
              const axes = 5;
              const labels = ['覆盖','准确','速度','稳定','可解释'];
              const current = [0.78, 0.65, 0.86, 0.72, 0.58];
              const target = [0.9, 0.85, 0.9, 0.85, 0.8];
              const R = 80;
              let grid = '';
              for (let r = 1; r <= 4; r++) {
                const points = Array.from({ length: axes }, (_, i) => {
                  const a = -Math.PI / 2 + i * 2 * Math.PI / axes;
                  return `${Math.cos(a) * R * r / 4},${Math.sin(a) * R * r / 4}`;
                }).join(' ');
                grid += `<polygon points="${points}" fill="none" stroke="var(--line)" stroke-dasharray="2 2"/>`;
              }
              let axisLines = '';
              for (let i = 0; i < axes; i++) {
                const a = -Math.PI / 2 + i * 2 * Math.PI / axes;
                axisLines += `<line x1="0" y1="0" x2="${Math.cos(a)*R}" y2="${Math.sin(a)*R}" stroke="var(--line)"/>`;
              }
              const polyPoints = (vals) => vals.map((v, i) => {
                const a = -Math.PI / 2 + i * 2 * Math.PI / axes;
                return `${Math.cos(a) * R * v},${Math.sin(a) * R * v}`;
              }).join(' ');
              const labelPts = labels.map((l, i) => {
                const a = -Math.PI / 2 + i * 2 * Math.PI / axes;
                return `<text class="atelier-axis-label" x="${Math.cos(a) * (R + 14)}" y="${Math.sin(a) * (R + 14)}" text-anchor="middle">${l}</text>`;
              }).join('');
              return grid + axisLines + labelPts
                + `<polygon points="${polyPoints(target)}" fill="rgba(8,127,115,0.15)" stroke="var(--teal)" stroke-dasharray="3 2"/>`
                + `<polygon points="${polyPoints(current)}" fill="rgba(8,127,115,0.35)" stroke="var(--teal-deep)" stroke-width="2"/>`;
            })()}
          </g>
        </svg>
      </div>
    </div>
  `));

  /* 漏斗图：5 阶段 */
  sample('chart-funnel', card('chart-funnel', 'Funnel · 漏斗', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">销售转化漏斗</div><div class="atelier-chart__sub">访问 → 成交</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          <g transform="translate(40 6)">
            ${[['访问 12,000', 100, '#087f73'], ['注册 4,800', 75, '#23836f'], ['试用 1,920', 56, '#5aa99e'], ['付费 480', 38, '#b9d4ce'], ['续费 168', 22, '#dce6e2']].map((s, i) => {
              const w = s[1] * 4.8;
              const x = 250 - w / 2 + 10;
              const y = i * 36;
              return `<polygon points="${x},${y} ${x+w},${y} ${x+w-12},${y+30} ${x+12},${y+30}" fill="${s[2]}"/><text class="atelier-axis-label" x="260" y="${y+19}" text-anchor="middle" fill="${i<2?'#fff':'#10243e'}">${s[0]}</text>`;
            }).join('')}
          </g>
        </svg>
      </div>
    </div>
  `));

  /* 散点图：3 群 */
  sample('chart-scatter', card('chart-scatter', 'Scatter · 散点 + 聚类', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">客户分布</div><div class="atelier-chart__sub">X：活跃天数 · Y：客单价</div></div>
        <div class="atelier-chart__legend"><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch"></i>高价值</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#5aa99e"></i>中等</span><span class="atelier-chart__legend__item"><i class="atelier-chart__legend__swatch" style="background:#b9d4ce"></i>流失</span></div>
      </div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','20','40','60','80'], 200)}
          ${(() => {
            const seed = (i) => ((Math.sin(i*999) + 1) / 2);
            let pts = '';
            [['#087f73', 30, 60], ['#5aa99e', 50, 35], ['#b9d4ce', 80, 18]].forEach(([color, off, count]) => {
              for (let i = 0; i < count; i++) {
                const x = 40 + off + seed(i) * 30;
                const y = 178 - seed(i + off) * 150;
                const r = 4 + seed(i + 17) * 3;
                pts += `<circle cx="${x}" cy="${y}" r="${r}" fill="${color}" fill-opacity="0.8"/>`;
              }
            });
            return pts;
          })()}
          ${xLabels(['0','30','60','90','120'], 600, 200)}
        </svg>
      </div>
    </div>
  `));

  /* 瀑布图 */
  sample('chart-waterfall', card('chart-waterfall', 'Waterfall · 瀑布', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">利润构成瀑布</div><div class="atelier-chart__sub">从收入到净利</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','20','40','60','80'], 200)}
          ${(() => {
            const items = [
              ['收入', 80, 0, '#087f73'],
              ['成本', -28, 80, '#b94a48'],
              ['费用', -12, 52, '#b94a48'],
              ['其他', 8, 40, '#23836f'],
              ['税', -6, 48, '#b94a48'],
              ['净利', 42, 0, '#10243e'],
            ];
            let cur = 0; let out = '';
            items.forEach((it, i) => {
              const x = 60 + i * 90;
              const y0 = 178 - cur;
              const h = it[1];
              const y1 = y0 - h;
              const top = h >= 0 ? y1 : y0;
              const color = it[3];
              out += `<rect x="${x}" y="${top}" width="60" height="${Math.abs(h)}" fill="${color}" fill-opacity="0.85"/>`;
              out += `<text class="atelier-axis-label" x="${x+30}" y="${top - 4}" text-anchor="middle">${h>=0?'+':''}${h}</text>`;
              out += `<text class="atelier-axis-label" x="${x+30}" y="194" text-anchor="middle">${it[0]}</text>`;
              if (h >= 0) cur += h;
            });
            return out;
          })()}
        </svg>
      </div>
    </div>
  `));

  /* 子弹图 */
  sample('chart-bullet', card('chart-bullet', 'Bullet · 子弹', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">KPI 完成进度</div><div class="atelier-chart__sub">实际 vs 目标 vs 区间</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','25','50','75','100'], 200)}
          ${[['销售额', 82, 90, [60, 80]], ['毛利率', 68, 75, [50, 70]], ['留存', 76, 80, [60, 75]], ['转化', 54, 60, [40, 55]]].map((s, i) => {
            const y = 30 + i * 36;
            const max = 110;
            const x = 50;
            const segW = max;
            return `<rect x="${x}" y="${y}" width="${segW * s[3][0]/100}" height="14" fill="#dce6e2"/>
              <rect x="${x}" y="${y}" width="${segW * s[3][1]/100 - segW * s[3][0]/100}" height="14" x="" fill="#b9d4ce"/>
              <rect x="${x}" y="${y}" width="${segW * s[1]/100}" height="6" y2="${y+6}" fill="#087f73"/>
              <line x1="${x + segW * s[2]/100}" y1="${y-3}" x2="${x + segW * s[2]/100}" y2="${y+17}" stroke="var(--amber)" stroke-width="2"/>
              <text class="atelier-axis-label" x="170" y="${y+10}">${s[0]} · 实际 ${s[1]} · 目标 ${s[2]}</text>`;
          }).join('')}
        </svg>
      </div>
    </div>
  `));

  sample('chart-combo', card('chart-combo', '柱+线组合 · 销售+客户数', `
    <div class="atelier-chart">
      <div class="atelier-chart__head"><div><div class="atelier-chart__title">销售 vs 客户数</div><div class="atelier-chart__sub">柱：销售（万）· 线：客户数</div></div></div>
      <div class="atelier-chart__body">
        <svg viewBox="0 0 600 200" preserveAspectRatio="none">
          ${axis(0,600,200).replace(/<\/?g[^>]*>/g, '')}
          ${yLabels(['0','10','20','30'], 200)}
          <g>
            <rect class="atelier-bars-bar" x="50" y="120" width="36" height="58"/>
            <rect class="atelier-bars-bar" x="150" y="100" width="36" height="78"/>
            <rect class="atelier-bars-bar" x="250" y="80" width="36" height="98"/>
            <rect class="atelier-bars-bar" x="350" y="60" width="36" height="118"/>
            <rect class="atelier-bars-bar" x="450" y="50" width="36" height="128"/>
            <rect class="atelier-bars-bar" x="550" y="40" width="36" height="138"/>
          </g>
          <path class="atelier-line atelier-line--compare" d="M68 160 L168 140 L268 120 L368 90 L468 70 L568 50" fill="none"/>
          <g fill="#293d53" stroke="#fff" stroke-width="1.5">
            <circle cx="68" cy="160" r="2.5"/><circle cx="168" cy="140" r="2.5"/><circle cx="268" cy="120" r="2.5"/><circle cx="368" cy="90" r="2.5"/><circle cx="468" cy="70" r="2.5"/><circle cx="568" cy="50" r="2.5"/>
          </g>
          ${xLabels(['6月','7月','8月','9月','10月','11月'], 600, 200)}
        </svg>
      </div>
    </div>
  `));

  /* ============================ 4 种报告表格 ============================ */
  function standardTableFoot(extra='') {
    return `<div class="atelier-table-foot"><span>显示 5 / 48 条${extra}</span><span>当前页 1 / 10</span></div>`;
  }

  sample('table-flat', card('table-flat', 'Table · 平铺模式（默认）', `
    <div class="atelier-table-wrap">
      <table class="atelier-table">
        <thead><tr><th class="is-sortable">月份<span class="atelier-th-sort">⇅</span></th><th>城市</th><th>品类</th><th class="num">销售额</th><th class="num">毛利率</th><th class="num is-up">环比</th></tr></thead>
        <tbody>
          <tr><td>2024-12</td><td>上海</td><td>电子产品</td><td class="num">2.40万</td><td class="num">31.2%</td><td class="num is-up">+14.8%</td></tr>
          <tr><td>2024-11</td><td>杭州</td><td>电子产品</td><td class="num">2.20万</td><td class="num">33.1%</td><td class="num is-up">+8.2%</td></tr>
          <tr><td>2024-10</td><td>杭州</td><td>服装鞋帽</td><td class="num">1.92万</td><td class="num">40.1%</td><td class="num is-up">+4.9%</td></tr>
          <tr><td>2024-09</td><td>南京</td><td>服装鞋帽</td><td class="num">1.64万</td><td class="num">42.6%</td><td class="num is-up">+18.5%</td></tr>
          <tr><td>2024-08</td><td>上海</td><td>电子产品</td><td class="num">1.18万</td><td class="num">27.8%</td><td class="num is-down">−31.4%</td></tr>
        </tbody>
      </table>
      ${standardTableFoot()}
    </div>
  `));

  sample('table-tree', card('table-tree', 'Table · 合并树模式（rowspan 父级）', `
    <div class="atelier-table-wrap">
      <table class="atelier-table atelier-table--tree">
        <thead><tr><th>部门</th><th>员工</th><th>项目</th><th>类型</th><th class="num">工时</th><th>日期</th></tr></thead>
        <tbody>
          <tr data-depth="1"><td rowspan="4">🏭 生产部</td><td rowspan="2">张三</td><td>A-001 生产线改造</td><td>直接</td><td class="num">168h</td><td>2024-07</td></tr>
          <tr data-depth="2"><td>A-002 设备维护</td><td>间接</td><td class="num">32h</td><td>2024-07</td></tr>
          <tr data-depth="2"><td>李四</td><td>A-001 生产线改造</td><td>直接</td><td class="num">176h</td><td>2024-07</td></tr>
          <tr data-depth="2"><td>王五</td><td>B-003 质量检测</td><td>直接</td><td class="num">152h</td><td>2024-07</td></tr>
          <tr class="is-subtotal"><td colspan="4" style="text-align:right">生产部小计</td><td class="num">528h</td><td></td></tr>
          <tr data-depth="1"><td rowspan="3">🔬 研发部</td><td rowspan="2">赵六</td><td>C-001 新功能开发</td><td>直接</td><td class="num">184h</td><td>2024-07</td></tr>
          <tr data-depth="2"><td>C-002 技术文档</td><td>间接</td><td class="num">16h</td><td>2024-07</td></tr>
          <tr data-depth="2"><td>孙七</td><td>C-001 新功能开发</td><td>直接</td><td class="num">160h</td><td>2024-07</td></tr>
          <tr class="is-subtotal"><td colspan="4" style="text-align:right">研发部小计</td><td class="num">360h</td><td></td></tr>
          <tr class="is-grand"><td colspan="4" style="text-align:right">📊 合计</td><td class="num">888h</td><td></td></tr>
        </tbody>
      </table>
    </div>
  `));

  sample('table-indent', card('table-indent', 'Table · 缩进层级模式', `
    <div class="atelier-table-wrap">
      <table class="atelier-table atelier-table--indent">
        <thead><tr><th>层级</th><th class="num">工时</th><th>占比</th></tr></thead>
        <tbody>
          <tr data-depth="1"><td>📊 总计</td><td class="num">888h</td><td>100%</td></tr>
          <tr data-depth="2"><td>🏭 生产部</td><td class="num">528h</td><td>59.5%</td></tr>
          <tr data-depth="3"><td>　　├ 张三</td><td class="num">200h</td><td>22.5%</td></tr>
          <tr data-depth="3"><td>　　│　　├ A-001 生产线改造</td><td class="num">168h</td><td></td></tr>
          <tr data-depth="3"><td>　　│　　└ A-002 设备维护</td><td class="num">32h</td><td></td></tr>
          <tr data-depth="3"><td>　　├ 李四</td><td class="num">176h</td><td>19.8%</td></tr>
          <tr data-depth="3"><td>　　│　　└ A-001 生产线改造</td><td class="num">176h</td><td></td></tr>
          <tr data-depth="3"><td>　　└ 王五</td><td class="num">152h</td><td>17.1%</td></tr>
          <tr data-depth="2"><td>🔬 研发部</td><td class="num">360h</td><td>40.5%</td></tr>
          <tr class="is-grand" data-depth="1"><td>📊 总计</td><td class="num">888h</td><td>100%</td></tr>
        </tbody>
      </table>
    </div>
  `));

  sample('table-cross', card('table-cross', 'Table · 交叉透视模式', `
    <div class="atelier-table-wrap">
      <table class="atelier-table atelier-table--cross">
        <thead>
          <tr><th class="is-corner">员工 \ 月</th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th class="is-total">合计</th></tr>
        </thead>
        <tbody>
          <tr><td class="is-row-h">张三</td><td>176</td><td>160</td><td>184</td><td>168</td><td>176</td><td>160</td><td class="is-total">1,224</td></tr>
          <tr><td class="is-row-h">李四</td><td>168</td><td>176</td><td>160</td><td>152</td><td>184</td><td>168</td><td class="is-total">1,184</td></tr>
          <tr><td class="is-row-h">王五</td><td>152</td><td>160</td><td>168</td><td>176</td><td>144</td><td>160</td><td class="is-total">1,112</td></tr>
          <tr><td class="is-row-h">赵六</td><td>184</td><td>176</td><td>192</td><td>168</td><td>176</td><td>184</td><td class="is-total">1,280</td></tr>
          <tr><td class="is-row-h">孙七</td><td>160</td><td>152</td><td>168</td><td>176</td><td>160</td><td>168</td><td class="is-total">1,144</td></tr>
          <tr class="is-total"><td class="is-row-h">合计</td><td>840</td><td>824</td><td>872</td><td>840</td><td>840</td><td>840</td><td class="is-total">5,944</td></tr>
        </tbody>
      </table>
    </div>
  `));

  sample('table-empty', card('table-empty', 'Table · 空态', `
    <div class="atelier-table-wrap">
      <table class="atelier-table"><thead><tr><th>月份</th><th>城市</th><th>品类</th><th class="num">销售额</th></tr></thead></table>
      <div class="atelier-empty-cell">
        <div style="font-size:24px;color:var(--line-2);margin-bottom:4px">○</div>
        <div>没有匹配的数据 · 调整筛选条件后再试</div>
      </div>
    </div>
  `));

  sample('table-loading', card('table-loading', 'Table · 加载态', `
    <div class="atelier-table-wrap" style="min-height:240px">
      <div style="padding:14px 16px;display:flex;align-items:center;gap:10px;color:var(--muted);border-bottom:1px solid var(--line);background:#fafbf8">
        <span class="atelier-spinner"></span>
        <span style="font-size:11px">正在加载华东 2024 全年数据…</span>
      </div>
      <div class="atelier-skeleton" style="height:34px;width:100%"></div>
      <div class="atelier-skeleton" style="height:14px;width:80%;margin:10px 16px"></div>
      <div class="atelier-skeleton" style="height:14px;width:90%;margin:0 16px 6px"></div>
      <div class="atelier-skeleton" style="height:14px;width:70%;margin:0 16px 6px"></div>
      <div class="atelier-skeleton" style="height:14px;width:85%;margin:0 16px 6px"></div>
      <div class="atelier-skeleton" style="height:14px;width:60%;margin:0 16px 12px"></div>
    </div>
  `));

  /* ============================ ReportPaper 两种 ============================ */
  sample('report-paper', card('report-paper', 'ReportPaper · 紧凑版', `
    <div class="atelier-paper">
      <div class="atelier-report__kicker">REPORT / v1</div>
      <div class="atelier-report__title">华东区域销售经营分析</div>
      <div class="atelier-report__meta">
        <span><i>数据范围</i>2024.01—2024.12</span>
        <span><i>分析范围</i>华东 · 全品类</span>
        <span><i>生成耗时</i>8.4s</span>
      </div>
      <div class="atelier-report__finding">
        <div class="atelier-report__finding-label">核心发现</div>
        <div class="atelier-report__finding-text">华东全年销售额保持增长，但 <strong>8 月出现阶段性回落</strong>；电子产品贡献 44%。</div>
      </div>
    </div>
  `));

  sample('report-paper-full', card('report-paper-full', 'ReportPaper · 完整版（KPI + 趋势 + 原因 + 异常 + 建议）', `
    <div class="atelier-report">
      <div class="atelier-report__kicker">REPORT / v1 · 2026-07-24</div>
      <div class="atelier-report__title">华东区域销售经营分析</div>
      <div class="atelier-report__meta">
        <span><i>数据范围</i>2024.01—2024.12</span>
        <span><i>分析范围</i>华东 · 全品类</span>
        <span><i>生成耗时</i>8.4s</span>
        <span><i>需求</i>来自会话 #A7B3</span>
      </div>
      <div class="atelier-report__finding">
        <div class="atelier-report__finding-label">核心发现</div>
        <div class="atelier-report__finding-text">华东全年销售额保持增长，但 <strong>8 月出现阶段性回落</strong>；电子产品贡献 44%，增长主要由上海与杭州驱动。</div>
      </div>
      <div class="atelier-kpi-grid atelier-kpi-grid--4" style="margin-top:14px">
        <div class="atelier-kpi"><div class="atelier-kpi__label">销售额</div><div class="atelier-kpi__value">32.8<span class="atelier-kpi__unit">万</span></div><div class="atelier-kpi__delta">↑ 18.6%</div></div>
        <div class="atelier-kpi"><div class="atelier-kpi__label">毛利</div><div class="atelier-kpi__value">10.4<span class="atelier-kpi__unit">万</span></div><div class="atelier-kpi__delta">↑ 14.2%</div></div>
        <div class="atelier-kpi"><div class="atelier-kpi__label">客单价</div><div class="atelier-kpi__value">6,842<span class="atelier-kpi__unit">元</span></div><div class="atelier-kpi__delta is-down">↓ 3.8%</div></div>
        <div class="atelier-kpi"><div class="atelier-kpi__label">毛利率</div><div class="atelier-kpi__value">31.7<span class="atelier-kpi__unit">%</span></div><div class="atelier-kpi__delta">↑ 1.4pp</div></div>
      </div>
      <div class="atelier-driver-list" style="margin-top:14px">
        <div class="atelier-driver"><span class="atelier-driver__no">01</span><span>上海高客单电子产品增长<small>MacBook 与手机集中在 Q3–Q4</small></span><strong>+5.2万</strong></div>
        <div class="atelier-driver"><span class="atelier-driver__no">02</span><span>杭州线上渠道扩张<small>订单量较上半年月均 +22%</small></span><strong>+2.8万</strong></div>
        <div class="atelier-driver"><span class="atelier-driver__no">03</span><span>服装鞋帽折扣改善<small>毛利率提升 2.4pp</small></span><strong>+1.1万</strong></div>
      </div>
      <div class="atelier-anomaly" style="margin-top:14px">
        <div class="atelier-anomaly__tag">ANOMALY · 8 月</div>
        <h3>销售额环比下降 24.1%，显著偏离全年趋势</h3>
        <p>主要由上海电子产品销量回落造成；9 月已恢复至趋势线附近。</p>
      </div>
      <div style="margin-top:14px;padding:14px;border:1px solid var(--line);background:#f6f8f6">
        <div style="font:800 8px var(--font-ui);letter-spacing:0.13em;color:var(--teal);margin-bottom:6px">SUGGESTED NEXT</div>
        <div class="atelier-suggestion" style="background:transparent;padding:0"><span>增加华南区域对比</span><span class="atelier-suggestion__arrow">↗</span></div>
      </div>
    </div>
  `));

  // ============================ COMPOSITE ============================
  sample('requirement', card('requirement', 'RequirementCard · 完整 missing 态', `
    <div class="atelier-reqcard atelier-reqcard--missing">
      <div class="atelier-reqcard__head">
        <div>
          <div class="atelier-reqcard__kicker">AGENT REQUIREMENT BRIEF</div>
          <div class="atelier-reqcard__title">需求解析与执行确认</div>
        </div>
        <span class="atelier-reqcard__status">需要补充 2 项</span>
      </div>
      <div class="atelier-reqcard__summary">分析销售趋势与异常波动（用户：帮我分析一下销量）</div>
      <div class="atelier-reqcard__body">
        <div class="atelier-reqcard__grid">
          <div><div class="atelier-reqcard__group-label">核心指标</div><div class="atelier-inline"><span class="atelier-tag">销售额</span></div></div>
          <div><div class="atelier-reqcard__group-label">时间与范围</div><div class="atelier-inline"><span class="atelier-tag atelier-tag--amber">时间待补充</span><span class="atelier-tag atelier-tag--amber">范围待补充</span></div></div>
        </div>
        <div class="atelier-missing-zone">
          <div class="atelier-missing-heading">
            <span class="atelier-missing-title">需要你确认的信息</span>
            <span class="atelier-missing-note">选项由后端根据当前问题返回</span>
          </div>
          <div class="atelier-option-group">
            <div class="atelier-option-label">时间范围</div>
            <div class="atelier-option-row">
              <button class="atelier-chip">本月</button>
              <button class="atelier-chip is-selected">2024 全年</button>
              <button class="atelier-chip">上月</button>
              <button class="atelier-chip">2025 全年</button>
            </div>
          </div>
          <div class="atelier-option-group">
            <div class="atelier-option-label">分析范围</div>
            <div class="atelier-option-row">
              <button class="atelier-chip">华东</button>
              <button class="atelier-chip is-selected">华南</button>
              <button class="atelier-chip">全国</button>
              <button class="atelier-chip">全部</button>
            </div>
          </div>
          <div class="atelier-assumption" style="margin-top:10px">
            <span>Agent 假设：按月环比并解释异常月份</span>
            <span class="atelier-assumption__actions">
              <button class="atelier-mini-btn" style="padding:3px 7px;border:1px solid #dec797;border-radius:4px;background:#fff8e9;font-size:9px">修改</button>
              <button class="atelier-mini-btn" style="padding:3px 7px;border:1px solid #acd5cc;border-radius:4px;background:var(--teal-soft);color:var(--teal-deep);font-size:9px">接受</button>
            </span>
          </div>
        </div>
      </div>
      <div class="atelier-reqcard__actions">
        <span class="atelier-reqcard__actions-hint">确认后 Agent 才会查询数据并生成报告</span>
        <span class="atelier-reqcard__actions-row">
          <button class="atelier-btn">继续对话补充</button>
          <button class="atelier-btn atelier-btn--primary">补充完成，查看确认</button>
        </span>
      </div>
    </div>
  `));

  sample('suggestion', card('suggestion', 'SuggestionChip · 推荐调整', `
    <div class="atelier-stack" style="max-width:360px">
      <button class="atelier-suggestion"><span>增加华南区域对比</span><span class="atelier-suggestion__arrow">↗</span></button>
      <button class="atelier-suggestion"><span>深入解释异常月份</span><span class="atelier-suggestion__arrow">↗</span></button>
      <button class="atelier-suggestion"><span>按 Top 商品贡献展示</span><span class="atelier-suggestion__arrow">↗</span></button>
    </div>
  `));

  sample('conversation', card('conversation', 'ConversationBubble · 用户/Agent 双向', `
    <div class="atelier-stack" style="max-width:560px">
      <div class="atelier-bubble--user" style="display:flex;justify-content:flex-end">
        <div class="atelier-bubble__body" style="background:var(--ink);color:#fff;border-color:var(--ink)">分析 2024 年华东销售趋势和异常。</div>
        <div class="atelier-bubble__avatar">你</div>
      </div>
      <div class="atelier-bubble" style="display:flex">
        <div class="atelier-bubble__avatar" style="background:var(--teal)">AI</div>
        <div>
          <div class="atelier-bubble__body" style="border-radius:3px 12px 12px 12px">我已识别 2024 年华东销售趋势，并准备好需求卡供你确认。</div>
          <div class="atelier-bubble__meta">ReportAgent · 需求分析</div>
        </div>
      </div>
    </div>
  `));

  document.querySelectorAll('[data-rate]').forEach((root) => {
    const stars = Array.from(root.querySelectorAll('.atelier-rate__star'));
    const set = (n) => stars.forEach((s) => s.classList.toggle('is-on', Number(s.dataset.rateI) <= n));
    stars.forEach((s) => s.addEventListener('click', () => set(Number(s.dataset.rateI))));
  });

  document.querySelectorAll('[data-stepper]').forEach((root) => {
    const input = root.querySelector('input');
    const btns = root.querySelectorAll('button');
    btns[0].addEventListener('click', () => { input.value = Math.max(Number(input.min || 0), Number(input.value) - 1); });
    btns[1].addEventListener('click', () => { input.value = Math.min(Number(input.max || 99), Number(input.value) + 1); });
  });

  document.querySelectorAll('[data-cascader]').forEach((root) => {
    const segs = root.querySelectorAll('.atelier-cascader__seg');
    segs.forEach((s) => s.addEventListener('change', () => Atelier.toast(`已选择 ${s.value}`, 'info')));
  });

  // Table sortable header (sort cycles asc / desc / none)
  document.querySelectorAll('.atelier-table thead th.is-sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const tbody = th.closest('table').querySelector('tbody');
      if (!tbody) return;
      const idx = Array.from(th.parentNode.children).indexOf(th);
      const asc = th.classList.contains('is-sorted-asc');
      const desc = th.classList.contains('is-sorted-desc');
      const order = asc ? -1 : desc ? 0 : 1;
      Array.from(th.parentNode.querySelectorAll('th.is-sortable')).forEach((x) => x.classList.remove('is-sorted-asc', 'is-sorted-desc'));
      if (order !== 0) th.classList.add(order === 1 ? 'is-sorted-asc' : 'is-sorted-desc');
      const rows = Array.from(tbody.children);
      const numeric = rows.every((r) => !isNaN(parseFloat((r.children[idx] || {}).textContent || '')));
      rows.sort((a, b) => {
        if (order === 0) return 0;
        const av = (a.children[idx] || {}).textContent.trim();
        const bv = (b.children[idx] || {}).textContent.trim();
        if (numeric) return order * (parseFloat(av) - parseFloat(bv));
        return order * av.localeCompare(bv, 'zh');
      });
      rows.forEach((r) => tbody.appendChild(r));
    });
  });

  document.querySelectorAll('.atelier-table tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => tr.classList.toggle('is-expand'));
  });

  sample('tabs', card('tabs', 'Tabs · 键盘方向键切换', `
    <div role="tablist" aria-label="视图模式" data-tabs-demo>
      <div class="atelier-tabs">
        <button class="atelier-tabs__btn is-active" role="tab" aria-selected="true" data-tab-idx="0">① 平铺</button>
        <button class="atelier-tabs__btn" role="tab" aria-selected="false" data-tab-idx="1">② 树形</button>
        <button class="atelier-tabs__btn" role="tab" aria-selected="false" data-tab-idx="2">③ 缩进</button>
        <button class="atelier-tabs__btn" role="tab" aria-selected="false" data-tab-idx="3">④ 交叉</button>
      </div>
      <div class="atelier-tabpanel" data-tab-panel>选择「树形」可看示例：使用 rowspan 与缩进进行父子分组。</div>
    </div>
  `));

  sample('steps', card('steps', 'Steps · 报告生成阶段', `
    <div class="atelier-steps" role="list">
      <div class="atelier-step is-done" role="listitem"><span class="atelier-step__dot">✓</span><span>需求确认</span></div>
      <div class="atelier-step__bar"></div>
      <div class="atelier-step is-current" role="listitem"><span class="atelier-step__dot">3</span><span>执行查询</span></div>
      <div class="atelier-step__bar"></div>
      <div class="atelier-step" role="listitem"><span class="atelier-step__dot">4</span><span>生成报告</span></div>
    </div>
  `));

  sample('breadcrumb', card('breadcrumb', 'Breadcrumb · 当前位置', `
    <nav class="atelier-breadcrumb" aria-label="面包屑">
      <a href="#">会话</a><span class="atelier-breadcrumb__sep">/</span>
      <a href="#">华东销售</a><span class="atelier-breadcrumb__sep">/</span>
      <a href="#">v3</a><span class="atelier-breadcrumb__sep">/</span>
      <span>摘要</span>
    </nav>
  `));

  sample('desclist', card('desclist', 'DescriptionList · 元数据展示', `
    <dl class="atelier-desclist" style="max-width:480px">
      <dt>分析对象</dt><dd>销售趋势与异常</dd>
      <dt>时间范围</dt><dd>2024.01 — 2024.12</dd>
      <dt>分析范围</dt><dd>华东 · 全品类 · 全渠道</dd>
      <dt>分析方法</dt><dd>趋势分析 · 异常检测 · 区域贡献</dd>
      <dt>生成耗时</dt><dd>8.4s</dd>
      <dt>SQL 状态</dt><dd>已校验 · 已执行</dd>
    </dl>
  `));

  sample('treeview', card('treeview', 'Treeview · 维度树', `
    <div class="atelier-treeview" role="tree" style="max-width:340px">
      <div class="atelier-treeview__node" data-depth="1" role="treeitem" aria-expanded="true">▾ 销售</div>
      <div class="atelier-treeview__node" data-depth="2" role="treeitem" tabindex="0">华东</div>
      <div class="atelier-treeview__node" data-depth="3" role="treeitem" tabindex="-1">上海</div>
      <div class="atelier-treeview__node" data-depth="3" role="treeitem" tabindex="-1">杭州</div>
      <div class="atelier-treeview__node" data-depth="2" role="treeitem" tabindex="0">华南</div>
      <div class="atelier-treeview__node" data-depth="1" role="treeitem" aria-expanded="false">▸ 库存</div>
      <div class="atelier-treeview__node" data-depth="1" role="treeitem" aria-expanded="false">▸ 退货</div>
    </div>
  `));

  sample('pagination', card('pagination', 'Pagination · 键盘可达', `
    <nav class="atelier-pagination" role="navigation" aria-label="分页">
      <button class="atelier-pagination__btn" aria-label="上一页">‹</button>
      <button class="atelier-pagination__btn is-active" aria-current="page" aria-label="第 1 页">1</button>
      <button class="atelier-pagination__btn" aria-label="第 2 页">2</button>
      <button class="atelier-pagination__btn" aria-label="第 3 页">3</button>
      <span class="atelier-pagination__ellipsis">…</span>
      <button class="atelier-pagination__btn" aria-label="第 9 页">9</button>
      <button class="atelier-pagination__btn" aria-label="下一页">›</button>
    </nav>
  `));

  sample('progress-circle', card('progress-circle', 'ProgressCircle · 环形进度', `
    <div class="atelier-demo-row">
      <div class="atelier-progress-circle" role="progressbar" aria-valuenow="72" aria-valuemin="0" aria-valuemax="100" data-circle="72" data-circle-tone="teal">
        <svg viewBox="0 0 64 64">
          <circle class="atelier-progress-circle__track" cx="32" cy="32" r="28"/>
          <circle class="atelier-progress-circle__fill" cx="32" cy="32" r="28" stroke-dasharray="126.6 175.9"/>
        </svg>
        <div class="atelier-progress-circle__text">72%</div>
      </div>
      <div class="atelier-progress-circle" role="progressbar" aria-valuenow="34" aria-valuemin="0" aria-valuemax="100" data-circle="34">
        <svg viewBox="0 0 64 64">
          <circle class="atelier-progress-circle__track" cx="32" cy="32" r="28"/>
          <circle class="atelier-progress-circle__fill" cx="32" cy="32" r="28" stroke-dasharray="59.7 175.9" style="stroke:var(--amber)"/>
        </svg>
        <div class="atelier-progress-circle__text">34%</div>
      </div>
      <div class="atelier-progress-circle" role="progressbar" aria-valuenow="100" aria-valuemin="0" aria-valuemax="100" data-circle="100">
        <svg viewBox="0 0 64 64">
          <circle class="atelier-progress-circle__track" cx="32" cy="32" r="28"/>
          <circle class="atelier-progress-circle__fill" cx="32" cy="32" r="28" stroke-dasharray="175.9 175.9" style="stroke:var(--green)"/>
        </svg>
        <div class="atelier-progress-circle__text">✓</div>
      </div>
    </div>
  `));

  sample('empty-variants', card('empty-variants', 'Empty · 4 种变体', `
    <div class="atelier-grid-3">
      <div class="atelier-empty">
        <div class="atelier-empty__icon">○</div>
        <div class="atelier-empty__title">暂无报告</div>
        <div class="atelier-empty__desc">完成一次分析后会自动出现</div>
      </div>
      <div class="atelier-empty atelier-empty--bordered">
        <div class="atelier-empty__icon" style="color:var(--amber)">⚠</div>
        <div class="atelier-empty__title">未匹配数据</div>
        <div class="atelier-empty__desc">调整筛选条件后再试</div>
        <div class="atelier-empty__action"><button class="atelier-btn">重置筛选</button></div>
      </div>
      <div class="atelier-empty atelier-empty--compact">
        <div class="atelier-empty__title">无模板</div>
      </div>
    </div>
  `));

  sample('skeleton-variants', card('skeleton-variants', 'Skeleton · 多种形态', `
    <div class="atelier-stack" style="max-width:360px">
      <div class="atelier-skeleton atelier-skeleton--title"></div>
      <div class="atelier-skeleton atelier-skeleton--text" style="width:90%"></div>
      <div class="atelier-skeleton atelier-skeleton--text" style="width:70%"></div>
      <div style="display:flex;gap:10px;align-items:center">
        <div class="atelier-skeleton atelier-skeleton--circle" style="width:36px;height:36px"></div>
        <div class="atelier-skeleton atelier-skeleton--text" style="flex:1"></div>
        <div class="atelier-skeleton atelier-skeleton--button"></div>
      </div>
    </div>
  `));

  sample('back-to-top', card('back-to-top', 'BackToTop · 滚动后出现', `
    <div class="atelier-demo-col" style="width:240px">
      <div style="font-size:11px;color:var(--muted)">滚动到页面底部时，圆按钮自动出现。点击平滑回到顶部。</div>
      <button class="atelier-back-to-top" aria-label="返回顶部" data-btt>↑</button>
    </div>
  `));

  /* ============================ A11Y 演示 ============================ */
  sample('focus-ring', card('focus-ring', 'Focus Ring · Tab 键可见焦点环', `
    <div class="atelier-a11y-demo">
      <div class="atelier-a11y-demo__row">
        <span>用 <span class="atelier-a11y-demo__kbd">Tab</span> 键在此区域内循环：</span>
        <button class="atelier-btn atelier-btn--primary">主操作</button>
        <button class="atelier-btn">次要</button>
        <input class="atelier-textfield" placeholder="输入" style="width:120px"/>
        <a href="#" style="font-size:11px">链接</a>
      </div>
      <div class="atelier-a11y-demo__row">点击 <span class="atelier-a11y-demo__kbd">点击此处激活焦点</span> 后，环形焦点环出现在所有交互元素上。</div>
    </div>
  `));

  sample('command-bar', card('command-bar', 'CommandBar · ⌘K 命令面板（键盘可达）', `
    <div class="atelier-demo-row">
      <button class="atelier-btn atelier-btn--primary" data-cmd-open>打开 ⌘K 面板</button>
      <span style="font-size:10px;color:var(--faint)">按 <span class="atelier-a11y-demo__kbd">⌘ K</span> 或 <span class="atelier-a11y-demo__kbd">Ctrl K</span> 也可触发</span>
    </div>
  `));

  sample('tab-keyboard', card('tab-keyboard', 'Tabs · 方向键切换', `
    <div class="atelier-tabs" role="tablist" aria-label="模式" data-tabs-kbd>
      <button class="atelier-tabs__btn is-active" role="tab" tabindex="0" aria-selected="true" data-tab-kbd-idx="0">① 平铺</button>
      <button class="atelier-tabs__btn" role="tab" tabindex="-1" aria-selected="false" data-tab-kbd-idx="1">② 树形</button>
      <button class="atelier-tabs__btn" role="tab" tabindex="-1" aria-selected="false" data-tab-kbd-idx="2">③ 缩进</button>
    </div>
    <div class="atelier-field__hint" style="margin-top:8px;font-size:10px">用 <span class="atelier-a11y-demo__kbd">←</span> <span class="atelier-a11y-demo__kbd">→</span> <span class="atelier-a11y-demo__kbd">Home</span> <span class="atelier-a11y-demo__kbd">End</span> 切换 Tab</div>
  `));

  sample('dialog-focus-trap', card('dialog-focus-trap', 'Dialog · 焦点圈 + ESC', `
    <div class="atelier-demo-row">
      <button class="atelier-btn" data-focus-trap-open>打开带焦点圈的 Dialog</button>
    </div>
  `));

  sample('dropdown-keyboard', card('dropdown-keyboard', 'Dropdown · 方向键 + 回车 + ESC', `
    <div class="atelier-demo-row">
      <button class="atelier-btn" data-a11y-dropdown>操作 ▾</button>
      <span style="font-size:10px;color:var(--faint)">Tab 进入 / <span class="atelier-a11y-demo__kbd">↑</span> <span class="atelier-a11y-demo__kbd">↓</span> 移动 / <span class="atelier-a11y-demo__kbd">Enter</span> 触发 / <span class="atelier-a11y-demo__kbd">Esc</span> 关闭</span>
    </div>
  `));

  sample('sr-only', card('sr-only', 'sr-only · 仅屏幕阅读器可见', `
    <div class="atelier-a11y-demo">
      <div class="atelier-a11y-demo__row">
        <span>状态指示器（视觉）：</span>
        <span class="atelier-spinner"></span>
        <span>已对屏幕阅读器声明：<code>&lt;span class="atelier-sr-only"&gt;加载中&lt;/span&gt;</code></span>
        <span class="atelier-sr-only">加载中</span>
        <span style="color:var(--green)">✓ sr-only 文本生效</span>
      </div>
      <div class="atelier-a11y-demo__row">
        <button class="atelier-icon-btn" aria-label="关闭"><span aria-hidden="true">×</span></button>
        <span>关闭按钮的视觉为「×」，但其 <code>aria-label="关闭"</code> 仍提供文字</span>
      </div>
    </div>
  `));

  // Tab 交互：视觉 + 键盘
  document.querySelectorAll('[data-tabs-demo]').forEach((root) => {
    const btns = root.querySelectorAll('.atelier-tabs__btn');
    const panel = root.querySelector('[data-tab-panel]');
    const labels = [
      '选择「平铺」：每行完整列出所有列。',
      '选择「树形」可看示例：使用 rowspan 与缩进进行父子分组。',
      '选择「缩进」：无合并、无重复，靠缩进表示层级归属。',
      '选择「交叉」：行=员工 · 列=月份 · 单元格=工时。',
    ];
    btns.forEach((b, i) => b.addEventListener('click', () => {
      btns.forEach((x) => { x.classList.remove('is-active'); x.setAttribute('aria-selected', 'false'); });
      b.classList.add('is-active');
      b.setAttribute('aria-selected', 'true');
      if (panel) panel.textContent = labels[i] || '';
    }));
  });

  // Tabs 键盘示例：方向键 + Home/End
  document.querySelectorAll('[data-tabs-kbd]').forEach((root) => {
    const btns = Array.from(root.querySelectorAll('.atelier-tabs__btn'));
    const labels = ['平铺模式', '树形模式', '缩进模式'];
    root.addEventListener('keydown', (e) => {
      const target = e.target;
      if (!btns.includes(target)) return;
      const i = btns.indexOf(target);
      let next = i;
      if (e.key === 'ArrowRight') next = (i + 1) % btns.length;
      else if (e.key === 'ArrowLeft') next = (i - 1 + btns.length) % btns.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = btns.length - 1;
      else return;
      e.preventDefault();
      btns[next].focus();
      btns[next].click();
    });
  });

  // ProgressCircle 渲染
  document.querySelectorAll('[data-circle]').forEach((el) => {
    const v = Number(el.dataset.circle || 0);
    const r = 28;
    const c = 2 * Math.PI * r;
    const fill = el.querySelector('.atelier-progress-circle__fill');
    if (fill) {
      const dash = (v / 100) * c;
      fill.setAttribute('stroke-dasharray', `${dash.toFixed(1)} ${c.toFixed(1)}`);
      if (v === 100) {
        const text = el.querySelector('.atelier-progress-circle__text');
        if (text) text.style.color = 'var(--green)';
      }
    }
  });

  // BackToTop 演示
  const btt = document.querySelector('[data-btt]');
  if (btt) {
    btt.style.position = 'static';
    btt.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  }

  // CommandBar ⌘K
  const cmdRoot = document.createElement('div');
  cmdRoot.className = 'atelier-command-overlay';
  cmdRoot.style.display = 'none';
  cmdRoot.innerHTML = `
    <div class="atelier-command-panel" role="dialog" aria-modal="true" aria-labelledby="cmdTitle">
      <div class="atelier-command-panel__head">
        <input class="atelier-command-panel__input" placeholder="搜索模板、动作、表…" aria-label="命令搜索" data-cmd-input/>
        <span class="atelier-command-panel__esc">esc</span>
      </div>
      <div class="atelier-command-panel__list" role="listbox" data-cmd-list></div>
      <div class="atelier-command-panel__foot">
        <span>↑↓ 选择</span><span>↵ 触发</span><span>esc 关闭</span>
      </div>
    </div>`;
  document.body.appendChild(cmdRoot);
  const cmdInput = cmdRoot.querySelector('[data-cmd-input]');
  const cmdList = cmdRoot.querySelector('[data-cmd-list]');
  const cmdItems = [
    { label: '新建分析', kbd: 'N', group: '导航' },
    { label: '打开模板中心', kbd: 'G T', group: '导航' },
    { label: '查看历史报告', kbd: 'G H', group: '导航' },
    { label: '切换数据域', kbd: 'D', group: '设置' },
    { label: '清空需求草稿', kbd: '⌘⇧⌫', group: '编辑' },
    { label: '导出当前报告为 PDF', kbd: '⌘E', group: '动作' },
  ];
  const renderCmd = (q) => {
    const list = q ? cmdItems.filter((it) => it.label.toLowerCase().includes(q.toLowerCase())) : cmdItems;
    cmdList.innerHTML = list.length
      ? list.map((it, i) => `<div class="atelier-command-panel__item ${i === 0 ? 'is-active' : ''}" role="option" tabindex="-1" data-cmd-idx="${i}">
          <span>${it.label}</span><span class="atelier-command-panel__item-tag">${it.kbd}</span><small>${it.group}</small>
        </div>`).join('')
      : '<div class="atelier-command-panel__item" style="color:var(--faint)">无匹配结果</div>';
    cmdList.dataset.active = 0;
  };
  const openCmd = () => {
    renderCmd('');
    cmdRoot.style.display = 'flex';
    cmdInput.value = '';
    setTimeout(() => cmdInput.focus(), 30);
  };
  const closeCmd = () => { cmdRoot.style.display = 'none'; };
  document.querySelector('[data-cmd-open]')?.addEventListener('click', openCmd);
  cmdInput.addEventListener('input', (e) => renderCmd(e.target.value));
  cmdInput.addEventListener('keydown', (e) => {
    const items = Array.from(cmdList.querySelectorAll('[data-cmd-idx]'));
    if (!items.length) return;
    let i = Number(cmdList.dataset.active || 0);
    if (e.key === 'ArrowDown') i = (i + 1) % items.length;
    else if (e.key === 'ArrowUp') i = (i - 1 + items.length) % items.length;
    else if (e.key === 'Enter') { items[i]?.click(); return; }
    else if (e.key === 'Escape') { closeCmd(); return; }
    else return;
    e.preventDefault();
    cmdList.dataset.active = i;
    items.forEach((it, j) => it.classList.toggle('is-active', j === i));
    items[i].scrollIntoView({ block: 'nearest' });
  });
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openCmd(); }
    else if (e.key === 'Escape' && cmdRoot.style.display === 'flex') { closeCmd(); }
  });
  cmdList.addEventListener('click', (e) => {
    const item = e.target.closest('[data-cmd-idx]');
    if (!item) return;
    Atelier.toast('已执行：' + cmdItems[Number(item.dataset.cmdIdx)].label, 'success');
    closeCmd();
  });

  // Dialog focus trap demo
  const trapBtn = document.querySelector('[data-focus-trap-open]');
  if (trapBtn) {
    trapBtn.addEventListener('click', () => {
      Atelier.openModal({
        title: '焦点圈演示',
        body: `<p style="font-size:11px;color:var(--muted);margin:0 0 8px">Tab 键将在输入框与按钮之间循环，ESC 关闭。</p>
          <label class="atelier-field"><span class="atelier-field__label">姓名</span><input class="atelier-textfield" value="admin"/></label>
          <label class="atelier-field" style="margin-top:8px"><span class="atelier-field__label">邮箱</span><input class="atelier-textfield" value="admin@example.com"/></label>`,
        footer: `<button class="atelier-btn" data-trap-cancel>取消</button><button class="atelier-btn atelier-btn--primary" data-trap-confirm>保存</button>`,
      });
      setTimeout(() => {
        const backdrops = document.querySelectorAll('.atelier-modal-backdrop');
        const last = backdrops[backdrops.length - 1];
        if (!last) return;
        last.addEventListener('click', (e) => {
          if (e.target.closest('[data-trap-cancel], [data-trap-confirm]')) Atelier.toast('操作已记录', 'success');
        });
      }, 60);
    });
  }

  // Dropdown a11y demo
  const ddBtn = document.querySelector('[data-a11y-dropdown]');
  if (ddBtn) {
    const items = [
      { label: '查看会话' },
      { label: '重新生成报告' },
      { divider: true },
      { label: '删除会话' },
    ];
    const open = () => Atelier.openDropdown(ddBtn, items, 'bottom-start');
    ddBtn.addEventListener('click', open);
    ddBtn.addEventListener('keydown', (e) => {
      if (['Enter', ' ', 'ArrowDown'].includes(e.key)) { e.preventDefault(); open(); }
    });
  }

  // 调整 plan 中提到的"先按原 chart sample"已全部替换为新 chart-* 示例。

  // ============================ Interactive bindings ============================
  document.querySelectorAll('[data-segmented]').forEach((root) => {
    const items = root.querySelectorAll('.atelier-segmented__item');
    items.forEach((btn) => btn.addEventListener('click', () => {
      items.forEach((b) => b.classList.remove('is-active'));
      btn.classList.add('is-active');
    }));
  });

  document.querySelectorAll('[data-tabbar]').forEach((root) => {
    const items = root.querySelectorAll('.atelier-tabbar__btn');
    items.forEach((btn) => btn.addEventListener('click', () => {
      items.forEach((b) => b.classList.remove('is-active'));
      btn.classList.add('is-active');
    }));
  });

  document.querySelectorAll('[data-progress]').forEach((bar) => {
    const v = Number(bar.dataset.progress || 0);
    const fill = bar.querySelector('.atelier-progress__fill');
    if (fill) fill.style.width = Math.max(0, Math.min(100, v)) + '%';
  });

  document.querySelectorAll('[data-slider]').forEach((slider) => {
    const out = slider.parentElement.querySelector('[data-slider-output]');
    if (out) out.textContent = (slider.value / 100).toFixed(2);
    slider.addEventListener('input', () => { if (out) out.textContent = (slider.value / 100).toFixed(2); });
  });

  document.querySelectorAll('[data-chip]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const group = btn.parentElement;
      group.querySelectorAll('.atelier-chip').forEach((c) => c.classList.remove('is-selected'));
      btn.classList.add('is-selected');
    });
  });

  document.querySelectorAll('[data-tooltip]').forEach((trigger) => {
    const txt = trigger.dataset.tooltip;
    Atelier.bindTooltip(trigger, txt);
  });

  document.querySelectorAll('[data-toast]').forEach((btn) => {
    const tone = btn.dataset.toast;
    const label = btn.textContent;
    btn.addEventListener('click', () => Atelier.toast(`${label} 提示示例`, tone));
  });

  document.querySelectorAll('[data-notif]').forEach((btn) => {
    const tone = btn.dataset.notif;
    btn.addEventListener('click', () => Atelier.notification({
      title: tone === 'success' ? '保存成功' : '需要补充 2 项',
      body: tone === 'success' ? '模板已添加到列表' : 'Agent 已识别业务目标，等待你确认时间与范围。',
      tone,
    }));
  });

  document.querySelector('[data-modal]').addEventListener('click', () => {
    Atelier.openModal({
      title: '新建分析模板',
      body: `<p style="font-size:12px;color:var(--muted);margin:0 0 8px">保存当前确认的需求为模板，后续可直接复用并仍需当次确认。</p>
        <label class="atelier-field"><span class="atelier-field__label">模板名称</span>
          <input class="atelier-textfield" placeholder="例如：华东月度销售分析"/>
        </label>
        <label class="atelier-field" style="margin-top:8px"><span class="atelier-field__label">说明（可选）</span>
          <textarea class="atelier-textarea" rows="2" placeholder="模板用途说明"></textarea>
        </label>`,
      footer: `<button class="atelier-btn" data-cancel>取消</button>
               <button class="atelier-btn atelier-btn--primary" data-confirm>创建</button>`,
    });
    document.querySelectorAll('[data-cancel]').forEach((b) => b.addEventListener('click', () => b.closest('.atelier-modal-backdrop').dispatchEvent(new Event('mousedown'))));
    document.querySelectorAll('[data-confirm]').forEach((b) => b.addEventListener('click', () => {
      Atelier.toast('已创建模板（原型演示）', 'success');
      b.closest('.atelier-modal-backdrop').dispatchEvent(new Event('mousedown'));
    }));
  });

  document.querySelector('[data-drawer]').addEventListener('click', () => {
    Atelier.openDrawer({
      title: '分析历史',
      body: `<div class="atelier-stack">
        <div class="atelier-card-inner">
          <b style="font:700 11px var(--font-display)">华东销售趋势 v2</b>
          <div class="atelier-field__hint">刚刚 · 仅回看</div>
        </div>
        <div class="atelier-card-inner">
          <b style="font:700 11px var(--font-display)">库存健康度 v1</b>
          <div class="atelier-field__hint">昨天</div>
        </div>
        <div class="atelier-card-inner">
          <b style="font:700 11px var(--font-display)">毛利结构 v1</b>
          <div class="atelier-field__hint">3 天前</div>
        </div>
      </div>`,
    });
  });

  document.querySelector('[data-popconfirm]').addEventListener('click', (e) => {
    Atelier.openPopconfirm(e.currentTarget, {
      title: '确认删除模板？',
      body: '删除后无法恢复，但不会影响已经生成的报告。',
      confirmLabel: '删除',
      cancelLabel: '取消',
      onConfirm: () => Atelier.toast('已删除（原型演示）', 'success'),
      onCancel: () => Atelier.toast('已取消', 'info'),
      placement: 'top',
    });
  });

  document.querySelector('[data-dropdown]').addEventListener('click', (e) => {
    Atelier.openDropdown(e.currentTarget, [
      { label: '查看会话' },
      { label: '重新生成报告' },
      { label: '保存为模板' },
      { divider: true },
      { label: '删除会话', onClick: () => Atelier.toast('已删除（原型演示）', 'success') },
    ]);
  });

  const ctxBox = document.querySelector('[data-contextmenu]');
  if (ctxBox) {
    ctxBox.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const fakeAnchor = { getBoundingClientRect: () => ({ top: e.clientY, left: e.clientX, right: e.clientX, bottom: e.clientY + 4, width: 0, height: 0 }) };
      Atelier.openDropdown(fakeAnchor, [
        { label: '复制' },
        { label: '粘贴' },
        { divider: true },
        { label: '导出报告' },
        { label: '删除' },
      ]);
    });
  }

  // Highlight current nav link in left rail
  document.querySelectorAll('.atelier-rail__link').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      document.querySelectorAll('.atelier-rail__link').forEach((b) => b.classList.remove('is-active'));
      a.classList.add('is-active');
      const target = document.querySelector(a.getAttribute('href'));
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
})();
