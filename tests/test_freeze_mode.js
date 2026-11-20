'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const appSource = fs.readFileSync('/var/www/layout-editor/app.js', 'utf8');

function createHarness() {
  const listeners = new Map();
  const elementsById = new Map();
  const sheets = [];
  const fetchCalls = [];
  const timers = new Map();
  let nextTimerId = 1;
  const storage = new Map();

  function makeEl(tag, id = null, cls = []) {
    const elListeners = new Map();
    const children = [];
    const clsSet = new Set(cls);
    const classList = {
      add: (...c) => c.forEach(x => clsSet.add(x)),
      remove: (...c) => c.forEach(x => clsSet.delete(x)),
      toggle: (c, force) => {
        if (force === undefined) {
          if (clsSet.has(c)) { clsSet.delete(c); return false; }
          clsSet.add(c); return true;
        }
        if (force) clsSet.add(c); else clsSet.delete(c);
        return Boolean(force);
      },
      contains: c => clsSet.has(c),
      toArray: () => Array.from(clsSet)
    };
    let cssText = '';
    let innerHtmlVal = '';
    const styleObj = {
      transform: '', opacity: '', zIndex: '', left: '', top: '', width: '', height: '',
      set cssText(val) {
        cssText = String(val || '');
        for (const prop of ['left', 'top', 'width', 'height', 'transform']) {
          const m = cssText.match(new RegExp(`${prop}\\s*:\\s*([^;]+)`));
          if (m) styleObj[prop] = m[1].trim();
        }
      },
      get cssText() { return cssText; }
    };
    const el = {
      tagName: String(tag || 'DIV').toUpperCase(),
      id: id || '',
      dataset: {},
      classList,
      get className() { return Array.from(clsSet).join(' '); },
      set className(val) {
        clsSet.clear();
        String(val || '').trim().split(/\s+/).filter(Boolean).forEach(c => clsSet.add(c));
      },
      style: styleObj,
      hidden: false,
      value: '',
      options: [],
      textContent: '',
      get innerHTML() { return innerHtmlVal; },
      set innerHTML(val) {
        innerHtmlVal = String(val || '');
        if (innerHtmlVal === '') children.length = 0;
      },
      children,
      parentElement: null,
      _attrs: {},
      setAttribute(k, v) { this._attrs[k] = String(v); },
      getAttribute(k) { return this._attrs[k] ?? null; },
      removeAttribute(k) { delete this._attrs[k]; },
      appendChild(child) {
        child.parentElement = this;
        children.push(child);
        if (child.id) elementsById.set(child.id, child);
        return child;
      },
      remove() {
        if (this.parentElement) {
          const idx = this.parentElement.children.indexOf(this);
          if (idx >= 0) this.parentElement.children.splice(idx, 1);
          this.parentElement = null;
        }
      },
      addEventListener(evt, fn) {
        if (!elListeners.has(evt)) elListeners.set(evt, []);
        elListeners.get(evt).push(fn);
      },
      dispatch(evt, eventObj = {}) {
        const list = elListeners.get(evt) || [];
        const e = Object.assign({
          preventDefault() {},
          stopPropagation() {},
          target: el,
          button: 0
        }, eventObj);
        if (typeof this['on' + evt] === 'function') this['on' + evt](e);
        list.forEach(fn => fn(e));
      },
      querySelector(sel) {
        return el.querySelectorAll(sel)[0] || null;
      },
      querySelectorAll(sel) {
        const out = [];
        const visit = node => {
          for (const ch of node.children) {
            if (matchesSel(ch, sel)) out.push(ch);
            visit(ch);
          }
        };
        visit(el);
        return out;
      },
      closest(sel) {
        let cur = el;
        while (cur) {
          if (matchesSel(cur, sel)) return cur;
          cur = cur.parentElement;
        }
        return null;
      },
      getBoundingClientRect() {
        return { left: 0, top: 0, width: 595.276, height: 841.89 };
      }
    };
    if (id) elementsById.set(id, el);
    return el;
  }

  function matchesSel(el, sel) {
    if (!sel) return false;
    const mPage = sel.match(/^\.pdf-page-sheet\[data-page="(\d+)"\]$/);
    if (mPage) return el.classList.contains('pdf-page-sheet') && el.dataset.page === mPage[1];
    if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
    if (sel.startsWith('#')) return el.id === sel.slice(1);
    return el.tagName.toLowerCase() === sel.toLowerCase();
  }

  const rootDoc = makeEl('body', 'root-body');
  const kanvas = makeEl('div', 'kanvas-pdf');
  const sheet1 = makeEl('div', null, ['pdf-page-sheet']);
  sheet1.dataset.page = '1';
  sheet1.setAttribute('data-page', '1');
  const overlay1 = makeEl('div', null, ['wasm-overlay-layer']);
  sheet1.appendChild(overlay1);
  kanvas.appendChild(sheet1);
  sheets.push(sheet1);
  rootDoc.appendChild(kanvas);

  const ids = [
    'aplikasi', 'auto-render-indicator', 'btn-buka-crop', 'btn-cut-mode',
    'btn-freeze-layout', 'btn-hapus-blok', 'btn-preview-mode', 'btn-redo', 'btn-reset-draft',
    'btn-simpan-server', 'btn-submit-bangun', 'btn-toggle-frames',
    'btn-toggle-grid', 'btn-undo', 'cari-objek', 'crop-coords-info',
    'crop-img', 'crop-rect', 'daftar-artikel', 'daftar-objek',
    'draft-status-pill', 'filter-jenis-objek', 'grup-prop-objek',
    'grup-prop-paragraf', 'grup-prop-teks', 'input-jurnal-baru',
    'judul-artikel', 'judul-modal-crop', 'layar-masuk', 'lbl-hal-aktif',
    'log-bangun', 'modal-baru', 'modal-crop', 'modal-metadata',
    'panel-properti', 'panel-tab-artikel', 'panel-tab-objek', 'pesan-masuk',
    'pindah-target', 'prop-break-before', 'prop-hal', 'prop-id', 'prop-info',
    'prop-jenis', 'prop-kaki-naik', 'prop-kaki-val', 'prop-kepala-turun',
    'prop-kepala-val', 'prop-keputusan', 'prop-move-pos', 'prop-nomor',
    'prop-penuh', 'prop-skala', 'prop-skala-val', 'prop-spasi-atas',
    'prop-spasi-bawah', 'prop-target-id', 'prop-teks', 'prop-teks-grup',
    'prop-tracking', 'prop-tracking-val', 'qa-masalah-box', 'qa-status',
    'server-loading-overlay', 'status-draft', 'tab-btn-artikel',
    'tab-btn-objek', 'toast-container', 'viewport-utama', 'zoom', 'zoom-val'
  ];
  ids.forEach(id => {
    if (!elementsById.has(id)) rootDoc.appendChild(makeEl('div', id));
  });

  const documentMock = {
    readyState: 'complete',
    body: rootDoc,
    activeElement: null,
    createElement: tag => makeEl(tag),
    getElementById: id => elementsById.get(id) || null,
    querySelector: sel => rootDoc.querySelector(sel),
    querySelectorAll: sel => rootDoc.querySelectorAll(sel),
    addEventListener(evt, fn) {
      if (!listeners.has(evt)) listeners.set(evt, []);
      listeners.get(evt).push(fn);
    }
  };

  const windowMock = {
    document: documentMock,
    location: { href: 'http://localhost/index.php' },
    pdfjsLib: null,
    confirm: () => true,
    addEventListener(evt, fn) {
      if (!listeners.has(evt)) listeners.set(evt, []);
      listeners.get(evt).push(fn);
    },
    dispatch(evt, eventObj = {}) {
      const e = Object.assign({ preventDefault() {}, stopPropagation() {} }, eventObj);
      (listeners.get(evt) || []).forEach(fn => fn(e));
    }
  };

  const ctx = {
    window: windowMock,
    document: documentMock,
    console,
    Math, Number, String, Boolean, Array, Object, Set, Map, JSON, Promise, Date,
    parseInt, parseFloat, isNaN, isFinite,
    CSS: { escape: s => String(s) },
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: k => storage.delete(k)
    },
    fetch: async (url, opts) => {
      fetchCalls.push({ url: String(url), opts });
      return { ok: true, status: 200, json: async () => ({ ok: true, versi: 'v2' }) };
    },
    setTimeout(fn, delay) {
      const id = nextTimerId++;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    setInterval: () => 0,
    clearInterval: () => {},
    URL,
    FormData: class {
      constructor() { this.entries = []; }
      append(k, v) { this.entries.push([k, v]); }
    }
  };
  ctx.globalThis = ctx;
  windowMock.EditorState = undefined;

  vm.createContext(ctx);
  vm.runInContext(appSource, ctx);

  function flushTimers() {
    let guard = 0;
    while (timers.size > 0 && guard++ < 20) {
      const entries = Array.from(timers.entries());
      timers.clear();
      for (const [, t] of entries) t.fn();
    }
  }

  async function flushAsync() {
    flushTimers();
    for (let i = 0; i < 10; i++) await Promise.resolve();
    flushTimers();
    for (let i = 0; i < 10; i++) await Promise.resolve();
  }

  return { ctx, windowMock, documentMock, elementsById, fetchCalls, flushTimers, flushAsync };
}

function loadSampleWorkspace(h) {
  const { ctx } = h;
  const S = ctx.window.EditorState;
  S.articleId = 'art-freeze-test';
  ctx.applyWorkspaceState({
    versi: 'v1',
    halaman: 1,
    halaman_awal: 1,
    blocks: [
      { id: 'm-judul', jenis: 'meta', judul: 'Judul', text: 'Sample Title', style: {} },
      { id: 'p-001', jenis: 'paragraph', judul: 'Paragraf 1', text: 'First sentence. Second sentence. Third sentence.', style: { freeze_x: 0, freeze_y: 0 } },
      { id: 'p-002', jenis: 'paragraph', judul: 'Paragraf 2', text: 'Untouched second paragraph text.', style: { freeze_x: 0, freeze_y: 0 } },
      { id: 'fig-001', jenis: 'figure', nomor: 1, judul: 'Figure 1', text: 'Caption 1', style: { freeze_x: 0, freeze_y: 0 } }
    ],
    layout_map: {
      halaman: 1,
      halaman_awal: 1,
      blocks: [
        { id: 'm-judul', type: 'meta', label: 'Judul', page: 1, column: 0, span: 'page', x: 76.54, y: 80.0, width: 442.2, height: 30.0 },
        { id: 'p-001', type: 'paragraph', label: 'Paragraf 1', page: 1, column: 1, span: 'column', x: 76.54, y: 200.0, width: 213.6, height: 55.0 },
        { id: 'p-002', type: 'paragraph', label: 'Paragraf 2', page: 1, column: 1, span: 'column', x: 76.54, y: 266.0, width: 213.6, height: 44.0 },
        { id: 'fig-001', type: 'figure', label: 'Figure 1', nomor: 1, page: 1, column: 2, span: 'column', x: 305.14, y: 200.0, width: 213.6, height: 110.0 }
      ]
    }
  }, false);
  return S;
}

function getBoxCoords(h) {
  const out = {};
  h.documentMock.querySelectorAll('.wasm-box').forEach(el => {
    out[el.dataset.id] = {
      left: el.style.left,
      top: el.style.top,
      width: el.style.width,
      height: el.style.height,
      transform: el.style.transform || ''
    };
  });
  return out;
}

function findBox(h, bid) {
  return h.documentMock.querySelectorAll('.wasm-box').find(el => el.dataset.id === bid);
}

(async () => {
  // 1. Freeze ON: zero fetch, zero draft edits, all non-meta locked, zero layout shift
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    const beforeCoords = getBoxCoords(h);
    assert(Object.keys(beforeCoords).length >= 4, 'Sample workspace must render overlay boxes');

    h.elementsById.get('btn-freeze-layout').dispatch('click');
    await h.flushAsync();

    assert.strictEqual(S.freezeLayout, true, 'Freeze mode must activate');
    assert.strictEqual(h.fetchCalls.length, 0, 'Clicking Freeze ON must NOT call fetch/save_workspace');
    assert.strictEqual(h.ctx.draftChangeCount(), 0, 'Clicking Freeze ON must NOT add fake draft edits');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('m-judul'), false, 'Meta block must stay unlocked');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-001'), true, 'p-001 must be locked');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-002'), true, 'p-002 must be locked');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('fig-001'), true, 'fig-001 must be locked');

    const afterCoords = getBoxCoords(h);
    assert.deepStrictEqual(afterCoords, beforeCoords, 'Overlay boxes must not shift when Freeze turns ON');
  }

  // 2. Single-click unlock / re-lock in Freeze mode (0 draft edits, 0 fetch)
  {
    const h = createHarness();
    loadSampleWorkspace(h);
    h.elementsById.get('btn-freeze-layout').dispatch('click');

    const box1 = findBox(h, 'p-001');
    box1.dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointerup', { clientX: 100, clientY: 210 });
    await h.flushAsync();

    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-001'), false, '1x click must unlock p-001');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-002'), true, 'p-002 must stay locked');
    assert.strictEqual(h.ctx.draftChangeCount(), 0, 'Unlocking without dragging must keep draft clean');
    assert.strictEqual(h.fetchCalls.length, 0, 'Unlocking must not call server');

    const box1Again = findBox(h, 'p-001');
    box1Again.dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointerup', { clientX: 100, clientY: 210 });
    await h.flushAsync();

    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-001'), true, 'Second 1x click must re-lock p-001');
    assert.strictEqual(h.ctx.draftChangeCount(), 0, 'Re-locking untouched block must keep draft clean');
    assert.strictEqual(h.fetchCalls.length, 0, 'Re-locking must not call server');
  }

  // 3. Dragging locked block is blocked; dragging unlocked block respects Grid 11pt snap & records ONLY that block with auto-render
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    h.elementsById.get('btn-freeze-layout').dispatch('click');

    // Attempt dragging locked block p-002
    const lockedBox = findBox(h, 'p-002');
    lockedBox.dispatch('pointerdown', { clientX: 100, clientY: 270 });
    h.windowMock.dispatch('pointermove', { clientX: 120, clientY: 295 });
    h.windowMock.dispatch('pointerup', { clientX: 120, clientY: 295 });
    await h.flushAsync();
    assert.strictEqual(h.ctx.draftChangeCount(), 0, 'Dragging locked block must be blocked');
    assert.strictEqual(h.fetchCalls.length, 0);

    // Unlock p-001, enable Grid 11pt, and drag
    findBox(h, 'p-001').dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointerup', { clientX: 100, clientY: 210 });
    S.showGrid = true;

    findBox(h, 'p-001').dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointermove', { clientX: 112, clientY: 229 }); // dy=19pt -> snaps to 22pt (2 * 11pt)
    h.windowMock.dispatch('pointerup', { clientX: 112, clientY: 229 });

    assert.deepStrictEqual(Object.keys(S.draft.edits), ['p-001'], 'Only moved block should be in draft.edits');
    assert.strictEqual(S.draft.edits['p-001'].style.freeze_x, 12);
    assert.strictEqual(S.draft.edits['p-001'].style.freeze_y, 22, 'Grid 11pt snap must snap freeze_y to multiple of 11pt');
    assert.strictEqual(S.draft.edits['p-001'].style.freeze_slot_h, 55, 'Moved block in Freeze must keep freeze_slot_h');
    assert.strictEqual(S.draft.edits['p-001'].style.unlocked_in_freeze, true);
    assert.strictEqual(S.draft.edits['p-001'].style.frozen, false);
    await h.flushAsync();
    assert(h.fetchCalls.some(c => c.url.includes('save_workspace')), 'Dragging unlocked block in Freeze must trigger auto-render save_workspace');
  }

  // 4. Cut Mode in Freeze splits locally without fetch, and both split pieces start locked
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    h.elementsById.get('btn-freeze-layout').dispatch('click');

    findBox(h, 'p-001').dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointerup', { clientX: 100, clientY: 210 });
    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-001'), false);

    h.ctx.executeVisualSplit('p-001', 0.5, false, {
      boxEl: findBox(h, 'p-001'),
      cutTarget: { snappedYPt: 222.0, totalLines: 5 }
    });
    await h.flushAsync();

    assert.strictEqual(h.fetchCalls.length, 0, 'Frozen local split must not call save_workspace');
    assert.strictEqual(S.draft.splits.length, 1);
    const newId = S.draft.splits[0].new_id;
    assert(newId, 'Split must create a local newId');
    assert.strictEqual(h.ctx.isBlockFrozenLocked('p-001'), true, 'Left split piece must start locked');
    assert.strictEqual(h.ctx.isBlockFrozenLocked(newId), true, 'Right split piece must start locked');

    // Turn Freeze OFF cleanly without fetch
    h.elementsById.get('btn-freeze-layout').dispatch('click');
    await h.flushAsync();
    assert.strictEqual(S.freezeLayout, false);
    assert.strictEqual(h.fetchCalls.length, 0, 'Turning Freeze OFF must not trigger fetch');
  }

  // 5. Preview Mode toggle ON hides chrome, Escape exits cleanly, zero fetch
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    const appEl = h.elementsById.get('aplikasi');
    const btnPrev = h.elementsById.get('btn-preview-mode');

    btnPrev.dispatch('click');
    await h.flushAsync();
    assert.strictEqual(S.previewMode, true, 'Preview Mode must activate');
    assert.strictEqual(appEl.classList.contains('preview-mode'), true, '#aplikasi must have .preview-mode');
    assert.strictEqual(btnPrev.classList.contains('btn-aktif'), true, '#btn-preview-mode must have .btn-aktif');
    assert.strictEqual(h.fetchCalls.length, 0, 'Preview Mode toggle must not call server');

    h.windowMock.dispatch('keydown', { key: 'Escape' });
    await h.flushAsync();
    assert.strictEqual(S.previewMode, false, 'Escape must exit Preview Mode');
    assert.strictEqual(appEl.classList.contains('preview-mode'), false);
    assert.strictEqual(btnPrev.classList.contains('btn-aktif'), false);
    assert.strictEqual(h.fetchCalls.length, 0, 'Exiting Preview Mode must not call server');
  }

  // 6. Resize unlocked text/paragraph (s, e, nw handles) in Freeze updates leading/freeze_w/freeze_x/freeze_y + freeze_slot_h & auto-renders
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    h.elementsById.get('btn-freeze-layout').dispatch('click');

    findBox(h, 'p-001').dispatch('pointerdown', { clientX: 100, clientY: 210 });
    h.windowMock.dispatch('pointerup', { clientX: 100, clientY: 210 });

    // Resize s handle (dy = +22pt)
    let box1 = findBox(h, 'p-001');
    h.ctx.onBoxHandleDown({ button: 0, clientX: 150, clientY: 255 }, box1, 's');
    h.windowMock.dispatch('pointermove', { clientX: 150, clientY: 277 });
    h.windowMock.dispatch('pointerup', { clientX: 150, clientY: 277 });

    let st1 = S.draft.edits['p-001'].style;
    assert.strictEqual(st1.freeze_slot_h, 55, 's-handle resize must preserve freeze_slot_h=55');
    assert.strictEqual(st1.spacing_above, undefined, 'Freeze text resize must NOT set spacing_above');
    assert.strictEqual(st1.spacing_below, undefined, 'Freeze text resize must NOT set spacing_below');
    assert(st1.leading > 0, 's-handle pull down must increase leading');
    await h.flushAsync();
    assert(h.fetchCalls.some(c => c.url.includes('save_workspace')), 'Text s-resize in Freeze must trigger auto-render');

    // Resize e handle (dx = -30pt)
    h.fetchCalls.length = 0;
    box1 = findBox(h, 'p-001');
    h.ctx.onBoxHandleDown({ button: 0, clientX: 290, clientY: 225 }, box1, 'e');
    h.windowMock.dispatch('pointermove', { clientX: 260, clientY: 225 });
    h.windowMock.dispatch('pointerup', { clientX: 260, clientY: 225 });

    st1 = S.draft.edits['p-001'].style;
    assert(Math.abs(st1.freeze_w - 183.6) < 0.2, `e-handle must update freeze_w (~183.6), got ${st1.freeze_w}`);
    await h.flushAsync();
    assert(h.fetchCalls.some(c => c.url.includes('save_workspace')), 'Text e-resize in Freeze must trigger auto-render');

    // Resize nw corner (dx = +10pt, dy = +11pt)
    h.fetchCalls.length = 0;
    box1 = findBox(h, 'p-001');
    h.ctx.onBoxHandleDown({ button: 0, clientX: 76.54, clientY: 200 }, box1, 'nw');
    h.windowMock.dispatch('pointermove', { clientX: 86.54, clientY: 211 });
    h.windowMock.dispatch('pointerup', { clientX: 86.54, clientY: 211 });

    st1 = S.draft.edits['p-001'].style;
    assert(st1.freeze_x > 0 && st1.freeze_y > 0, 'nw-handle inward drag must shift freeze_x and freeze_y');
    await h.flushAsync();
    assert(h.fetchCalls.some(c => c.url.includes('save_workspace')), 'Text nw-resize in Freeze must trigger auto-render');
  }

  // 7. Resize unlocked figure in Freeze includes freeze_slot_h & triggers auto-render
  {
    const h = createHarness();
    const S = loadSampleWorkspace(h);
    h.elementsById.get('btn-freeze-layout').dispatch('click');

    findBox(h, 'fig-001').dispatch('pointerdown', { clientX: 350, clientY: 240 });
    h.windowMock.dispatch('pointerup', { clientX: 350, clientY: 240 });

    const figBox = findBox(h, 'fig-001');
    h.ctx.onBoxHandleDown({ button: 0, clientX: 518, clientY: 310 }, figBox, 'se');
    h.windowMock.dispatch('pointermove', { clientX: 480, clientY: 290 });
    h.windowMock.dispatch('pointerup', { clientX: 480, clientY: 290 });

    const stFig = S.draft.edits['fig-001'].style;
    assert.strictEqual(stFig.freeze_slot_h, 110, 'Figure resize in Freeze must preserve freeze_slot_h=110');
    assert.strictEqual(stFig.unlocked_in_freeze, true);
    assert.strictEqual(stFig.frozen, false);
    await h.flushAsync();
    assert(h.fetchCalls.some(c => c.url.includes('save_workspace')), 'Figure resize in Freeze must trigger auto-render');
  }

  console.log('Freeze & Preview mode: 7 JavaScript regression scenarios passed');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
