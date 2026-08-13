// ============================================================================
// SchemaView — one instance per schema section, rendered in document order.
// Canvas (spatial map) + focus (one-table drill-down) per section.
// Cross-section FKs render as stub chips; clicking one jumps to the owning
// section and opens that table in focus. SchemaView.open(tableId) is the
// global navigation entry point.
// ============================================================================

(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svgEl(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    return e;
  }

  const registry = {};   // tableId -> { open(id), container }

  window.SchemaView = {
    render,
    open(tableId) {
      const entry = registry[tableId];
      if (!entry) return;
      entry.container.scrollIntoView({ block: 'start', behavior: 'smooth' });
      entry.open(tableId);
    },
    modes: (typeof WRITE_MODES !== 'undefined') ? WRITE_MODES : {},
  };

  function modeBadge(mode) {
    const m = WRITE_MODES[mode];
    if (!m) return null;
    const b = el('span', 'sv-mode mode-' + mode, m.short);
    b.title = m.label + ' — ' + m.desc;
    return b;
  }

  function render(container, section) {
    const tablesById = Object.fromEntries(section.tables.map((t) => [t.id, t]));
    const stubs = section.stubs || [];
    const stubIds = new Set(stubs.map((s) => s.table));

    // Edges: table FK → (table in section | stub), plus stub-declared edges.
    const edges = [];
    section.tables.forEach((t) => t.cols.forEach((c) => {
      if (!c.fk) return;
      const target = c.fk.split('.')[0];
      if (target === t.id) return;                       // self-reference: skip drawing
      if (tablesById[target] || stubIds.has(target)) {
        edges.push({ from: t.id, fromCol: c.n, to: target, toCol: c.fk.split('.')[1] });
      }
    }));
    stubs.forEach((s) => (s.edges || []).forEach((e) => {
      edges.push({ from: s.table, fromCol: e.fromCol, to: e.to, toCol: 'id', fromStub: true });
    }));

    const sv = el('div', 'sv');
    container.appendChild(sv);

    /* ---------------- top: search ---------------- */
    const top = el('div', 'sv-top');
    const search = el('div', 'sv-search');
    const q = document.createElement('input');
    q.type = 'search'; q.placeholder = 'search this section…'; q.autocomplete = 'off';
    const hits = el('span', 'hits', '');
    search.append(q, hits);
    top.appendChild(search);

    /* ---------------- canvas ---------------- */
    const canvasWrap = el('div', 'sv-canvas-wrap');
    const toolbar = el('div', 'sv-toolbar');
    const btnIn = el('button', 'sv-btn', '+');
    const btnOut = el('button', 'sv-btn', '−');
    const btnFit = el('button', 'sv-btn', 'fit');
    const btnCompact = el('button', 'sv-btn', 'keys only');
    toolbar.append(btnIn, btnOut, btnFit, btnCompact, top);
    const viewport = el('div', 'sv-viewport');
    if (section.viewportH) viewport.style.height = section.viewportH + 'px';
    const world = el('div', 'sv-world');
    const svg = svgEl('svg', { class: 'sv-edges', width: 2400, height: 1600 });
    const spot = el('div', 'sv-spot');
    world.appendChild(svg);
    viewport.append(world, spot);
    canvasWrap.append(toolbar, viewport,
      el('p', 'sv-hint', 'hover to trace · click a table to open it · double-click to spotlight · scroll to zoom, drag to pan · dashed chips live in other sections — click to jump'));
    sv.appendChild(canvasWrap);

    const nodeEls = {};   // id -> element (tables + stubs)
    const geom = {};      // id -> {x, y, w}

    section.tables.forEach((t) => {
      geom[t.id] = { x: t.x, y: t.y, w: t.w };
      const deg = edges.filter((e) => e.from === t.id || e.to === t.id).length;
      const box = el('div', 'sv-table');
      box.style.left = t.x + 'px'; box.style.top = t.y + 'px'; box.style.width = t.w + 'px';
      box.dataset.id = t.id;
      const head = el('div', 'sv-table-head');
      head.appendChild(el('span', 'sv-table-name', t.name));
      const mb = modeBadge(t.mode);
      if (mb) head.appendChild(mb);
      head.appendChild(el('span', 'sv-deg', deg + ''));
      box.appendChild(head);
      const ul = el('ul', 'sv-cols');
      t.cols.forEach((c) => {
        const li = el('li', 'sv-col' + (c.pk ? ' pk' : '') + (c.fk ? ' fk' : ''));
        li.dataset.col = c.n;
        li.appendChild(el('span', 'k', c.pk && c.fk ? 'PF' : c.pk ? 'PK' : c.fk ? 'FK' : ''));
        li.appendChild(el('span', 'n', c.n));
        li.appendChild(el('span', 't', c.t));
        ul.appendChild(li);
      });
      box.appendChild(ul);
      world.appendChild(box);
      nodeEls[t.id] = box;
    });

    stubs.forEach((s) => {
      geom[s.table] = { x: s.x, y: s.y, w: 210 };
      const box = el('div', 'sv-table sv-stub');
      box.style.left = s.x + 'px'; box.style.top = s.y + 'px'; box.style.width = '210px';
      box.dataset.id = s.table;
      const head = el('div', 'sv-table-head');
      head.appendChild(el('span', 'sv-table-name', s.table));
      head.appendChild(el('span', 'sv-stub-jump', '§ ↗'));
      box.appendChild(head);
      box.title = 'Lives in another section — click to jump';
      world.appendChild(box);
      nodeEls[s.table] = box;
    });

    function colY(id, colName) {
      const box = nodeEls[id];
      const g = geom[id];
      const li = box.querySelector && box.querySelector('.sv-col[data-col="' + colName + '"]');
      if (!li || li.offsetParent === null) return g.y + box.offsetHeight / 2;
      return g.y + li.offsetTop + li.offsetHeight / 2;
    }

    function drawEdges() {
      svg.innerHTML = '';
      edges.forEach((e) => {
        const src = geom[e.from], dst = geom[e.to];
        if (!src || !dst) return;
        const sy = colY(e.from, e.fromCol), dy = colY(e.to, e.toCol || 'id');
        let sx, dx, c1x, c2x;
        const gap = 46;
        if (dst.x > src.x + src.w + 20) { sx = src.x + src.w; dx = dst.x; c1x = sx + gap; c2x = dx - gap; }
        else if (dst.x + dst.w < src.x - 20) { sx = src.x; dx = dst.x + dst.w; c1x = sx - gap; c2x = dx + gap; }
        else { sx = src.x; dx = dst.x; const out = Math.min(sx, dx) - 56; c1x = out; c2x = out; }
        const g = svgEl('g', { class: 'sv-edge' + (e.fromStub || stubIds.has(e.to) ? ' xsec' : ''), 'data-from': e.from, 'data-to': e.to, 'data-fromcol': e.fromCol });
        const d = 'M ' + sx + ' ' + sy + ' C ' + c1x + ' ' + sy + ', ' + c2x + ' ' + dy + ', ' + dx + ' ' + dy;
        g.appendChild(svgEl('path', { class: 'sv-edge-halo', d }));
        g.appendChild(svgEl('path', { class: 'sv-edge-line', d }));
        g.appendChild(svgEl('circle', { class: 'sv-edge-one', cx: dx, cy: dy, r: 3.5 }));
        const dir = sx <= c1x ? 1 : -1;
        const fx = sx + dir * 9;
        g.appendChild(svgEl('path', { class: 'sv-edge-many',
          d: 'M ' + fx + ' ' + sy + ' L ' + sx + ' ' + (sy - 5) + ' M ' + fx + ' ' + sy + ' L ' + sx + ' ' + sy + ' M ' + fx + ' ' + sy + ' L ' + sx + ' ' + (sy + 5) }));
        const tt = document.createElementNS(NS, 'title');
        tt.textContent = e.from + '.' + e.fromCol + '  →  ' + e.to + '.' + (e.toCol || 'id') + '   (many → one)';
        g.appendChild(tt);
        g.addEventListener('mouseenter', () => {
          Object.entries(nodeEls).forEach(([id, b]) => b.classList.add(
            (id === e.from || id === e.to) ? 'hi' : 'dim'));
          svg.querySelectorAll('.sv-edge').forEach((x) => x.classList.add(x === g ? 'hi' : 'dim'));
        });
        g.addEventListener('mouseleave', clearHi);
        svg.appendChild(g);
      });
    }

    function clearHi() { sv.querySelectorAll('.hi,.dim').forEach((n) => n.classList.remove('hi', 'dim')); }
    function related(id) {
      const set = new Set([id]);
      edges.forEach((e) => { if (e.from === id) set.add(e.to); if (e.to === id) set.add(e.from); });
      return set;
    }
    Object.entries(nodeEls).forEach(([id, box]) => {
      box.addEventListener('mouseenter', () => {
        clearHi();
        const rel = related(id);
        Object.entries(nodeEls).forEach(([k, b]) => b.classList.add(rel.has(k) ? 'hi' : 'dim'));
        svg.querySelectorAll('.sv-edge').forEach((g) =>
          g.classList.add(g.dataset.from === id || g.dataset.to === id ? 'hi' : 'dim'));
      });
      box.addEventListener('mouseleave', clearHi);
      if (!box.classList.contains('sv-stub')) {
        box.addEventListener('dblclick', () => toggleSpot(id));
      }
    });

    /* spotlight */
    let spotId = null;
    function toggleSpot(id) {
      if (pendingFocus) { clearTimeout(pendingFocus); pendingFocus = null; }
      spotId = spotId === id ? null : id;
      sv.querySelectorAll('.ghost').forEach((n) => n.classList.remove('ghost'));
      spot.classList.toggle('on', !!spotId);
      if (!spotId) return;
      const keep = related(spotId);
      Object.entries(nodeEls).forEach(([k, b]) => { if (!keep.has(k)) b.classList.add('ghost'); });
      svg.querySelectorAll('.sv-edge').forEach((g) => {
        if (!(g.dataset.from === spotId || g.dataset.to === spotId)) g.classList.add('ghost');
      });
      spot.textContent = 'spotlight: ' + spotId + ' · double-click to exit';
    }

    /* search */
    q.addEventListener('input', () => {
      const term = q.value.trim().toLowerCase();
      sv.querySelectorAll('.smatch,.sdim').forEach((n) => n.classList.remove('smatch', 'sdim'));
      sv.querySelectorAll('.sv-col.match').forEach((n) => n.classList.remove('match'));
      if (!term) { hits.textContent = ''; return; }
      const matched = new Set();
      section.tables.forEach((t) => {
        const box = nodeEls[t.id];
        let hit = t.name.toLowerCase().includes(term);
        t.cols.forEach((c) => {
          if (c.n.toLowerCase().includes(term)) {
            hit = true;
            const li = box.querySelector('.sv-col[data-col="' + c.n + '"]');
            if (li) li.classList.add('match');
          }
        });
        if (hit) matched.add(t.id);
      });
      Object.entries(nodeEls).forEach(([k, b]) => b.classList.add(matched.has(k) ? 'smatch' : 'sdim'));
      svg.querySelectorAll('.sv-edge').forEach((g) => {
        if (!(matched.has(g.dataset.from) && matched.has(g.dataset.to))) g.classList.add('sdim');
      });
      hits.textContent = matched.size + '/' + section.tables.length;
    });
    q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { q.value = ''; q.dispatchEvent(new Event('input')); e.stopPropagation(); } });

    /* pan / zoom */
    const view = { x: 0, y: 0, k: 1 };
    function applyView() { world.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.k + ')'; }
    function fit() {
      let x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
      Object.entries(geom).forEach(([id, g]) => {
        const h = nodeEls[id].offsetHeight;
        x1 = Math.min(x1, g.x); y1 = Math.min(y1, g.y);
        x2 = Math.max(x2, g.x + g.w); y2 = Math.max(y2, g.y + h);
      });
      x1 -= 60; y1 -= 30; x2 += 30; y2 += 30;
      const vw = viewport.clientWidth, vh = viewport.clientHeight;
      const k = Math.min(vw / (x2 - x1), vh / (y2 - y1), 1.1);
      view.k = k;
      view.x = (vw - (x2 - x1) * k) / 2 - x1 * k;
      view.y = (vh - (y2 - y1) * k) / 2 - y1 * k;
      applyView();
    }
    function zoomAt(cx, cy, f) {
      const nk = Math.min(2.5, Math.max(0.3, view.k * f));
      const wx = (cx - view.x) / view.k, wy = (cy - view.y) / view.k;
      view.k = nk; view.x = cx - wx * nk; view.y = cy - wy * nk;
      applyView();
    }
    viewport.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = viewport.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0012));
    }, { passive: false });

    let drag = null, pendingFocus = null;
    viewport.addEventListener('pointerdown', (e) => {
      if (pendingFocus) { clearTimeout(pendingFocus); pendingFocus = null; }
      const hit = e.target.closest ? e.target.closest('.sv-table') : null;
      drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false,
               hitId: hit ? hit.dataset.id : null, hitStub: hit ? hit.classList.contains('sv-stub') : false };
      viewport.setPointerCapture(e.pointerId);
    });
    viewport.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) { view.x = drag.vx + dx; view.y = drag.vy + dy; applyView(); viewport.classList.add('panning'); }
    });
    viewport.addEventListener('pointerup', () => {
      viewport.classList.remove('panning');
      if (drag && !drag.moved && drag.hitId) {
        const id = drag.hitId, isStub = drag.hitStub;
        if (isStub) {
          window.SchemaView.open(id);
        } else {
          pendingFocus = setTimeout(() => { pendingFocus = null; openFocus(id); }, 260);
        }
      }
      drag = null;
    });
    btnIn.addEventListener('click', () => zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, 1.25));
    btnOut.addEventListener('click', () => zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, 0.8));
    btnFit.addEventListener('click', fit);
    btnCompact.addEventListener('click', () => {
      const on = sv.classList.toggle('compact');
      btnCompact.classList.toggle('active', on);
      btnCompact.textContent = on ? 'all columns' : 'keys only';
      drawEdges();
    });

    /* ---------------- focus mode ---------------- */
    const focus = el('div', 'sv-focus');
    const bar = el('div', 'sv-focus-bar');
    const btnBack = el('button', 'sv-back', '← back to map');
    const crumbs = el('div', 'sv-crumbs');
    const cycle = el('div', 'sv-cycle');
    const btnPrev = el('button', 'sv-btn', '‹ prev');
    const pos = el('span', 'pos', '');
    const btnNext = el('button', 'sv-btn', 'next ›');
    cycle.append(btnPrev, pos, btnNext);
    bar.append(btnBack, crumbs, cycle);
    const fcCanvas = el('div', 'sv-focus-canvas');
    const fcSvg = svgEl('svg', {});
    const fcCols = el('div', 'fc-cols');
    fcCanvas.append(fcSvg, fcCols);
    const fcMeta = el('div', 'fc-meta');
    focus.append(bar, fcCanvas, fcMeta);
    sv.appendChild(focus);

    let focusId = null, trail = [];

    // For focus neighbors we use ALL declared FKs on the table (not only the
    // ones drawable on this canvas), so cross-section references always appear.
    function neighborsOf(id) {
      const t = tablesById[id];
      const outs = [];
      t.cols.forEach((c) => {
        if (c.fk) {
          const target = c.fk.split('.')[0];
          if (target !== id) outs.push({ table: target, via: c.n, ext: !tablesById[target] });
        }
      });
      const ins = [];
      SCHEMA_SECTIONS.forEach((sec) => sec.tables.forEach((ot) => {
        if (ot.id === id) return;
        ot.cols.forEach((c) => {
          if (c.fk && c.fk.split('.')[0] === id) {
            ins.push({ table: ot.id, via: c.n, ext: !tablesById[ot.id] });
          }
        });
      }));
      return { ins, outs };
    }

    function neighborCard(n, dir) {
      const d = el('div', 'fc-card' + (n.ext ? ' fc-card-ext' : ''));
      d.appendChild(el('div', 'fc-card-name', n.table));
      const via = el('div', 'fc-card-via');
      via.innerHTML = (dir === 'in' ? 'via its <b>' + n.via + '</b>' : 'via <b>' + n.via + '</b>') +
        (n.ext ? ' <span class="fc-ext-tag">§ jump</span>' : '');
      d.appendChild(via);
      d.addEventListener('click', () => {
        if (n.ext) window.SchemaView.open(n.table);
        else hop(n.table);
      });
      return d;
    }

    function renderFocus(id) {
      focusId = id;
      const t = tablesById[id];

      crumbs.innerHTML = '';
      trail.concat([id]).forEach((tid, i, arr) => {
        const c = el('button', 'sv-crumb' + (i === arr.length - 1 ? ' here' : ''), tid);
        if (i < arr.length - 1) c.addEventListener('click', () => { trail = trail.slice(0, i); renderFocus(tid); });
        crumbs.appendChild(c);
        if (i < arr.length - 1) crumbs.appendChild(el('span', 'sv-crumb-sep', '→'));
      });
      const idx = section.tables.findIndex((x) => x.id === id);
      pos.textContent = (idx + 1) + ' / ' + section.tables.length;

      const nb = neighborsOf(id);
      fcCols.innerHTML = '';
      const inWrap = el('div', 'fc-side');
      inWrap.appendChild(el('div', 'fc-side-label', '← referenced by'));
      if (!nb.ins.length) inWrap.appendChild(el('div', 'fc-empty', 'nothing references this table'));
      nb.ins.forEach((n) => inWrap.appendChild(neighborCard(n, 'in')));

      const mid = el('div');
      const card = el('div', 'fc-focus-card');
      const head = el('div', 'fc-head');
      head.appendChild(el('span', 'fc-name', t.name));
      const mb = modeBadge(t.mode);
      if (mb) head.appendChild(mb);
      if (t.meta && t.meta.layer) head.appendChild(el('span', 'badge layer', t.meta.layer));
      card.appendChild(head);
      const ul = el('ul', 'fc-focus-cols');
      t.cols.forEach((c) => {
        const li = el('li', c.pk ? 'pk' : '');
        li.appendChild(el('b', null, c.pk && c.fk ? 'PF' : c.pk ? 'PK' : c.fk ? 'FK' : ''));
        const nm = el('span', null, c.n);
        if (c.note) { nm.title = c.note; nm.classList.add('has-note'); }
        li.appendChild(nm);
        li.appendChild(el('span', 't', c.t + (c.fk ? ' → ' + c.fk : '')));
        ul.appendChild(li);
      });
      card.appendChild(ul);
      mid.appendChild(card);

      const outWrap = el('div', 'fc-side');
      outWrap.appendChild(el('div', 'fc-side-label', 'references →'));
      if (!nb.outs.length) outWrap.appendChild(el('div', 'fc-empty', 'references nothing'));
      nb.outs.forEach((n) => outWrap.appendChild(neighborCard(n, 'out')));

      fcCols.append(inWrap, mid, outWrap);

      fcMeta.innerHTML = '';
      const left = el('div', 'card');
      left.appendChild(el('h4', null, 'why it exists'));
      left.appendChild(el('p', 'rationale', t.comment || '—'));
      if ((t.uniques && t.uniques.length) || (t.indexes && t.indexes.length)) {
        const chips = el('div', 'fc-chips');
        (t.uniques || []).forEach((u) => chips.appendChild(el('span', 'badge neutral', 'unique ' + u)));
        (t.indexes || []).forEach((u) => chips.appendChild(el('span', 'badge neutral', 'index ' + u)));
        left.appendChild(chips);
      }
      fcMeta.appendChild(left);
      const right = el('div', 'card');
      right.appendChild(el('h4', null, 'metadata'));
      const dl = el('dl', 'fc-kv');
      const m = WRITE_MODES[t.mode];
      if (m) {
        dl.appendChild(el('dt', null, 'write mode'));
        const dd = el('dd');
        dd.appendChild(el('span', 'sv-mode mode-' + t.mode, m.label));
        dd.appendChild(document.createTextNode(' — ' + m.desc));
        dl.appendChild(dd);
      }
      Object.entries(t.meta || {}).forEach(([k, v]) => {
        dl.appendChild(el('dt', null, k.replace(/_/g, ' ')));
        dl.appendChild(el('dd', null, v));
      });
      right.appendChild(dl);
      fcMeta.appendChild(right);

      requestAnimationFrame(drawFocusEdges);
      setTimeout(drawFocusEdges, 250);
    }

    function drawFocusEdges() {
      fcSvg.innerHTML = '';
      const fcCard = fcCanvas.querySelector('.fc-focus-card');
      if (!fcCard || !sv.classList.contains('mode-focus')) return;
      const rr = fcCanvas.getBoundingClientRect();
      const fr = fcCard.getBoundingClientRect();
      function link(x1, y1, x2, y2) {
        const m = (x1 + x2) / 2;
        fcSvg.appendChild(svgEl('path', { class: 'fc-edge', d: 'M ' + x1 + ' ' + y1 + ' C ' + m + ' ' + y1 + ', ' + m + ' ' + y2 + ', ' + x2 + ' ' + y2 }));
        fcSvg.appendChild(svgEl('circle', { class: 'fc-edge-dot', cx: x2, cy: y2, r: 3.5 }));
      }
      fcCanvas.querySelectorAll('.fc-side:first-child .fc-card').forEach((c) => {
        const r = c.getBoundingClientRect();
        link(r.right - rr.left, r.top + r.height / 2 - rr.top, fr.left - rr.left, fr.top + fr.height / 2 - rr.top);
      });
      fcCanvas.querySelectorAll('.fc-side:last-child .fc-card').forEach((c) => {
        const r = c.getBoundingClientRect();
        link(fr.right - rr.left, fr.top + fr.height / 2 - rr.top, r.left - rr.left, r.top + r.height / 2 - rr.top);
      });
    }

    function openFocus(id) {
      trail = [];
      sv.classList.add('mode-focus');
      renderFocus(id);
    }
    function hop(id) {
      if (id === focusId) return;
      trail.push(focusId);
      if (trail.length > 6) trail = trail.slice(-6);
      renderFocus(id);
    }
    function closeFocus() {
      sv.classList.remove('mode-focus');
      focusId = null;
    }
    btnBack.addEventListener('click', closeFocus);
    btnPrev.addEventListener('click', () => {
      const i = section.tables.findIndex((x) => x.id === focusId);
      hop(section.tables[(i - 1 + section.tables.length) % section.tables.length].id);
    });
    btnNext.addEventListener('click', () => {
      const i = section.tables.findIndex((x) => x.id === focusId);
      hop(section.tables[(i + 1) % section.tables.length].id);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sv.classList.contains('mode-focus')) closeFocus();
    });
    window.addEventListener('resize', () => {
      drawEdges();
      if (sv.classList.contains('mode-focus')) drawFocusEdges();
    });

    section.tables.forEach((t) => { registry[t.id] = { open: openFocus, container }; });

    function ready() { drawEdges(); fit(); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { drawEdges(); fit(); });
    requestAnimationFrame(ready);
    setTimeout(ready, 300);
  }
})();
