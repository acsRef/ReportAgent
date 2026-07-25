/* Atelier component library — vanilla JS APIs.
   Each public API is namespaced under window.Atelier. */

(function (global) {
  'use strict';

  // ============================ helpers ============================
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v == null) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else if (k === 'dataset' && typeof v === 'object') Object.assign(node.dataset, v);
        else node.setAttribute(k, v);
      }
    }
    if (children) {
      const arr = Array.isArray(children) ? children : [children];
      arr.forEach((c) => {
        if (c == null) return;
        node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
      });
    }
    return node;
  }

  function focusable(root) {
    if (!root) return [];
    return Array.from(root.querySelectorAll(
      'a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ));
  }

  function createContainer(className, role) {
    const div = document.createElement('div');
    div.className = className;
    if (role) div.setAttribute('role', role);
    document.body.appendChild(div);
    return div;
  }

  // ============================ Toast ============================
  const toastRoot = () => document.getElementById('toastRoot');
  const toast = (message, tone = 'teal', duration = 2200) => {
    const t = el('div', { class: `atelier-toast is-${tone}` });
    t.textContent = message;
    toastRoot().appendChild(t);
    requestAnimationFrame(() => t.classList.add('is-show'));
    setTimeout(() => {
      t.classList.remove('is-show');
      setTimeout(() => t.remove(), 250);
    }, duration);
  };
  toast.success = (m, d) => toast(m, 'green', d);
  toast.error = (m, d) => toast(m, 'red', d);
  toast.warning = (m, d) => toast(m, 'amber', d);
  toast.info = toast;

  // ============================ Notification ============================
  const notifRoot = () => document.getElementById('notifRoot');
  const notification = (opts) => {
    const { title, body, tone = 'teal', duration = 3200 } = typeof opts === 'string' ? { title: opts } : opts;
    const card = el('div', { class: `atelier-notif is-${tone}` });
    const head = el('div', null, [el('div', { class: 'atelier-notif__title', text: title })]);
    if (body) head.appendChild(el('div', { class: 'atelier-notif__body', text: body }));
    card.appendChild(head);
    notifRoot().appendChild(card);
    requestAnimationFrame(() => card.classList.add('is-show'));
    setTimeout(() => {
      card.classList.remove('is-show');
      setTimeout(() => card.remove(), 250);
    }, duration);
  };
  notification.success = (o) => notification(Object.assign({}, o, { tone: 'green' }));
  notification.error = (o) => notification(Object.assign({}, o, { tone: 'red' }));
  notification.warning = (o) => notification(Object.assign({}, o, { tone: 'amber' }));

  // ============================ Modal / Drawer ============================
  const openModal = (opts) => {
    const { title, body, footer, onClose, dismissOnBackdrop = true, dismissOnEsc = true } = opts;
    const root = document.getElementById('modalRoot');
    const backdrop = el('div', { class: 'atelier-modal-backdrop' });
    const panel = el('div', { class: 'atelier-modal-panel', role: 'dialog', 'aria-modal': 'true' });
    panel.setAttribute('aria-labelledby', `modal-title-${Date.now()}`);
    const titleId = panel.getAttribute('aria-labelledby');

    const header = el('div', { class: 'atelier-modal-header' }, [
      el('div', { class: 'atelier-modal-title', id: titleId, text: title }),
      el('button', { class: 'atelier-modal-close', 'aria-label': '关闭', html: '×' }),
    ]);
    const bodyEl = el('div', { class: 'atelier-modal-body' });
    if (typeof body === 'string') bodyEl.innerHTML = body;
    else if (body) bodyEl.appendChild(body);
    const footerEl = el('div', { class: 'atelier-modal-footer' });
    if (typeof footer === 'string') footerEl.innerHTML = footer;
    else if (footer) footerEl.appendChild(footer);
    else footerEl.style.display = 'none';

    panel.appendChild(header);
    panel.appendChild(bodyEl);
    if (footer) panel.appendChild(footerEl);
    backdrop.appendChild(panel);
    root.appendChild(backdrop);

    const previouslyFocused = document.activeElement;
    requestAnimationFrame(() => {
      backdrop.classList.add('is-open');
      const f = focusable(panel)[0];
      if (f) f.focus();
    });

    const close = () => {
      backdrop.classList.remove('is-open');
      setTimeout(() => {
        backdrop.remove();
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        if (onClose) onClose();
      }, 200);
    };
    panel.querySelector('.atelier-modal-close').addEventListener('click', close);
    if (dismissOnBackdrop) backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    if (dismissOnEsc) {
      const escHandler = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', escHandler); } };
      document.addEventListener('keydown', escHandler);
    }
    return { close };
  };

  const openDrawer = (opts) => {
    const { title, body, onClose, dismissOnBackdrop = true, dismissOnEsc = true } = opts;
    const root = document.getElementById('drawerRoot');
    const backdrop = el('div', { class: 'atelier-drawer-backdrop' });
    const panel = el('div', { class: 'atelier-drawer-panel', role: 'dialog', 'aria-modal': 'true' });
    panel.setAttribute('aria-labelledby', `drawer-title-${Date.now()}`);
    const titleId = panel.getAttribute('aria-labelledby');
    const header = el('div', { class: 'atelier-drawer-header' }, [
      el('div', { class: 'atelier-modal-title', id: titleId, text: title }),
      el('button', { class: 'atelier-modal-close', 'aria-label': '关闭', html: '×' }),
    ]);
    const bodyEl = el('div', { class: 'atelier-drawer-body' });
    if (typeof body === 'string') bodyEl.innerHTML = body;
    else if (body) bodyEl.appendChild(body);
    panel.appendChild(header);
    panel.appendChild(bodyEl);
    root.appendChild(backdrop);
    root.appendChild(panel);
    requestAnimationFrame(() => backdrop.classList.add('is-open') || panel.classList.add('is-open'));

    const previouslyFocused = document.activeElement;
    requestAnimationFrame(() => { const f = focusable(panel)[0]; if (f) f.focus(); });

    const close = () => {
      panel.classList.remove('is-open');
      backdrop.classList.remove('is-open');
      setTimeout(() => {
        backdrop.remove(); panel.remove();
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        if (onClose) onClose();
      }, 200);
    };
    panel.querySelector('.atelier-modal-close').addEventListener('click', close);
    if (dismissOnBackdrop) backdrop.addEventListener('click', close);
    if (dismissOnEsc) {
      const escHandler = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', escHandler); } };
      document.addEventListener('keydown', escHandler);
    }
    return { close };
  };

  // ============================ Popover / Popconfirm / Dropdown ============================
  const popoverRoot = () => document.getElementById('popoverRoot');

  function placeElement(el, anchor, placement = 'bottom-start') {
    const r = anchor.getBoundingClientRect();
    const pop = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    let top = r.bottom + 6, left = r.left;
    if (placement === 'bottom-end') left = r.right - pop.width;
    if (placement === 'top') top = r.top - pop.height - 6;
    if (placement === 'top-start') { top = r.top - pop.height - 6; left = r.left; }
    if (placement === 'top-end') { top = r.top - pop.height - 6; left = r.right - pop.width; }
    if (left + pop.width > vw - 8) left = vw - pop.width - 8;
    if (left < 8) left = 8;
    if (top + pop.height > vh - 8) top = r.top - pop.height - 6;
    if (top < 8) top = r.bottom + 6;
    el.style.top = top + 'px';
    el.style.left = left + 'px';
  }

  const openDropdown = (anchor, items, placement = 'bottom-start') => {
    const pop = el('div', { class: 'atelier-popover', role: 'menu' });
    items.forEach((it) => {
      if (it.divider) { pop.appendChild(el('div', { class: 'atelier-popover__divider' })); return; }
      const btn = el('button', { class: 'atelier-popover__item', role: 'menuitem', text: it.label });
      btn.addEventListener('click', () => { close(); if (it.onClick) it.onClick(); });
      pop.appendChild(btn);
    });
    popoverRoot().appendChild(pop);
    requestAnimationFrame(() => { placeElement(pop, anchor, placement); pop.classList.add('is-open'); });

    const close = () => { pop.classList.remove('is-open'); setTimeout(() => pop.remove(), 150); };
    const onDoc = (e) => { if (!pop.contains(e.target) && e.target !== anchor) { close(); document.removeEventListener('mousedown', onDoc); } };
    const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
    setTimeout(() => {
      document.addEventListener('mousedown', onDoc);
      document.addEventListener('keydown', onKey);
    }, 0);
    return { close };
  };

  const openPopconfirm = (anchor, opts) => {
    const { title, body, confirmLabel = '确认', cancelLabel = '取消', onConfirm, onCancel, placement = 'top' } = opts;
    const pop = el('div', { class: 'atelier-popconfirm', role: 'alertdialog' });
    pop.appendChild(el('div', { class: 'atelier-popconfirm__title', text: title || '确认' }));
    if (body) pop.appendChild(el('div', { class: 'atelier-popconfirm__body', text: body }));
    const actions = el('div', { class: 'atelier-popconfirm__actions' });
    const cancelBtn = el('button', { class: 'atelier-btn', text: cancelLabel });
    const confirmBtn = el('button', { class: 'atelier-btn atelier-btn--danger', text: confirmLabel });
    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    pop.appendChild(actions);
    popoverRoot().appendChild(pop);
    requestAnimationFrame(() => { placeElement(pop, anchor, placement); });

    const close = () => { pop.style.opacity = '0'; pop.style.transform = 'translateY(-4px)'; setTimeout(() => pop.remove(), 150); };
    cancelBtn.addEventListener('click', () => { close(); if (onCancel) onCancel(); });
    confirmBtn.addEventListener('click', () => { close(); if (onConfirm) onConfirm(); });
    const onDoc = (e) => { if (!pop.contains(e.target) && e.target !== anchor) { close(); document.removeEventListener('mousedown', onDoc); } };
    const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
    setTimeout(() => {
      document.addEventListener('mousedown', onDoc);
      document.addEventListener('keydown', onKey);
    }, 0);
    confirmBtn.focus();
    return { close };
  };

  // ============================ Tooltip ============================
  const bindTooltip = (trigger, content) => {
    const tip = el('div', { class: 'atelier-tooltip-bubble', role: 'tooltip' });
    tip.textContent = content;
    document.body.appendChild(tip);
    let timer = null;
    const show = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        placeElement(tip, trigger, 'top');
        tip.classList.add('is-open');
      }, 200);
    };
    const hide = () => { clearTimeout(timer); tip.classList.remove('is-open'); };
    trigger.addEventListener('mouseenter', show);
    trigger.addEventListener('mouseleave', hide);
    trigger.addEventListener('focus', show);
    trigger.addEventListener('blur', hide);
  };

  // ============================ Segmented control ============================
  const bindSegmented = (root) => {
    const items = Array.from(root.querySelectorAll('.atelier-segmented__item'));
    items.forEach((btn, i) => {
      btn.addEventListener('click', () => {
        items.forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
      });
    });
  };

  // ============================ Tabs ============================
  const bindTabbar = (root) => {
    const items = Array.from(root.querySelectorAll('.atelier-tabbar__btn'));
    items.forEach((btn) => {
      btn.addEventListener('click', () => {
        items.forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
      });
    });
  };

  // ============================ Progress ============================
  const bindProgress = (bar, value) => {
    const fill = bar.querySelector('.atelier-progress__fill');
    fill.style.width = Math.max(0, Math.min(100, value)) + '%';
  };

  // ============================ Slider ============================
  const bindSlider = (slider, output) => {
    const update = () => { if (output) output.textContent = slider.value; };
    slider.addEventListener('input', update);
    update();
  };

  // ============================ TextField / TextArea ============================
  const bindTextField = (input) => {
    input.addEventListener('input', () => {
      if (input.value.trim() === '') input.classList.add('is-empty');
      else input.classList.remove('is-empty');
    });
  };

  // ============================ Public API ============================
  global.Atelier = {
    toast,
    notification,
    openModal,
    openDrawer,
    openDropdown,
    openPopconfirm,
    bindTooltip,
    bindSegmented,
    bindTabbar,
    bindProgress,
    bindSlider,
    bindTextField,
  };
})(window);
