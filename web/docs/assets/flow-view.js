// ============================================================================
// FlowView — one dataflow, two renderings: "Stages" (cards with steps) and
// "Decision graph" (spine + gates + exits). Which one opens first is declared
// per flow in the data file (flow.defaultView: 'stages' | 'graph').
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

  window.FlowView = { render };

  function render(container, flow) {
    const root = el('div', 'fv');
    container.appendChild(root);

    const head = el('div', 'fv-head');
    head.appendChild(el('span', 'fv-title', flow.title));
    const hasGraph = !!flow.graph;
    let tabStages, tabGraph;
    if (hasGraph) {
      const tabs = el('div', 'fv-tabs');
      tabStages = el('button', 'fv-tab', 'Stages');
      tabGraph = el('button', 'fv-tab', 'Decision graph');
      tabs.append(tabStages, tabGraph);
      head.appendChild(tabs);
    }
    root.appendChild(head);
    if (flow.intro) root.appendChild(el('p', 'fv-lede', flow.intro));

    const stagesEl = el('div', 'fv-stages-wrap');
    const graphEl = el('div', 'fv-graph-wrap');
    root.append(stagesEl, graphEl);

    buildStages(stagesEl, flow);
    if (hasGraph) buildGraph(graphEl, flow.graph);

    function setView(v) {
      stagesEl.style.display = v === 'stages' ? '' : 'none';
      graphEl.style.display = v === 'graph' ? '' : 'none';
      if (tabStages) {
        tabStages.classList.toggle('active', v === 'stages');
        tabGraph.classList.toggle('active', v === 'graph');
      }
    }
    setView(hasGraph && flow.defaultView === 'graph' ? 'graph' : 'stages');
    if (hasGraph) {
      tabStages.addEventListener('click', () => setView('stages'));
      tabGraph.addEventListener('click', () => setView('graph'));
    }

    if (flow.aside) {
      const aside = el('aside', 'callout');
      aside.appendChild(el('h4', null, flow.aside.title));
      aside.appendChild(el('p', null, flow.aside.body));
      root.appendChild(aside);
    }
  }

  /* ------------------------------------------------------------- stages */
  function buildStages(wrap, flow) {
    const grid = el('div', 'fv-stages');
    flow.stages.forEach((st) => {
      const stage = el('div', 'fv-stage');
      const head = el('div', 'fv-stage-head');
      head.appendChild(el('span', 'fv-stage-label', st.label));
      head.appendChild(el('span', 'fv-stage-sub', st.sub || ''));
      stage.appendChild(head);
      const ol = el('ol', 'fv-steps');
      st.steps.forEach((s) => {
        const li = el('li', 'fv-step' + (s.skip ? ' has-skip' : ''));
        li.tabIndex = 0;
        li.appendChild(el('span', 'fv-num', String(s.n)));
        const body = el('div', 'fv-step-body');
        body.appendChild(el('h4', 'fv-step-title', s.title));
        body.appendChild(el('p', 'fv-step-desc', s.desc));
        if (s.skip) {
          const sk = el('p', 'fv-skip');
          sk.appendChild(el('span', 'fv-skip-label', 'if skipped'));
          sk.appendChild(document.createTextNode(' ' + s.skip));
          body.appendChild(sk);
        }
        li.appendChild(body);
        li.addEventListener('click', () => li.classList.toggle('pinned'));
        ol.appendChild(li);
      });
      stage.appendChild(ol);
      grid.appendChild(stage);
    });
    wrap.appendChild(grid);
  }

  /* -------------------------------------------------------------- graph */
  function buildGraph(wrap, graph) {
    const W = graph.width || 620, MX = graph.spineX || 210, NW = 190, NH = 46;
    const EX = graph.exitX || 470, EW = 140;
    const H = graph.height || (Math.max(...graph.nodes.map((n) => n.y)) + 60);

    const grid = el('div', 'fv-graph');
    const svgCard = el('div', 'fv-graph-svg');
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svgCard.appendChild(svg);
    const panelWrap = el('div', 'fv-graph-panel');
    const panel = el('div', 'card');
    panelWrap.appendChild(panel);
    grid.append(svgCard, panelWrap);
    wrap.appendChild(grid);

    svg.innerHTML = '<defs><marker id="fv-arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 8 4 L 0 8 z" fill="#9aa3b5"/></marker></defs>';
    function addPath(d, cls) {
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('class', 'gedge ' + (cls || ''));
      p.setAttribute('d', d);
      p.setAttribute('marker-end', 'url(#fv-arr)');
      svg.appendChild(p);
      return p;
    }
    function addLabel(x, y, text, cls, anchor) {
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('class', 'glabel ' + (cls || ''));
      t.setAttribute('x', x); t.setAttribute('y', y);
      if (anchor) t.setAttribute('text-anchor', anchor);
      t.textContent = text;
      svg.appendChild(t);
    }

    const N = graph.nodes;
    for (let i = 0; i < N.length - 1; i++) {
      const a = N[i], b = N[i + 1];
      const y1 = a.y + (a.gate ? 26 : NH / 2), y2 = b.y - (b.gate ? 26 : NH / 2);
      addPath('M ' + MX + ' ' + y1 + ' L ' + MX + ' ' + (y2 - 2));
      if (a.gate && a.spineLabel) addLabel(MX + 7, (y1 + y2) / 2 + 3, a.spineLabel);
    }
    (graph.exits || []).forEach((x) => {
      const g = N.find((n) => n.id === x.at);
      addPath('M ' + (MX + 26) + ' ' + g.y + ' L ' + (EX - EW / 2 - 4) + ' ' + x.y, 'branch');
      if (x.branchLabel) addLabel(MX + 40, Math.min(g.y, x.y) - 6 + (x.y > g.y ? 26 : 0), x.branchLabel, 'yes');
      if (x.rejoin) {
        const r = N.find((n) => n.id === x.rejoin);
        addPath('M ' + EX + ' ' + (x.y + 20) + ' C ' + (EX + 90) + ' ' + (x.y + 220) + ', ' + (EX + 60) + ' ' + (r.y - 120) + ', ' + (MX + NW / 2 + 4) + ' ' + (r.y - 6), 'branch');
      }
      if (x.loop) {
        const r = N.find((n) => n.id === x.loop);
        addPath('M ' + EX + ' ' + (x.y + 20) + ' C ' + (EX + 40) + ' ' + (x.y + 90) + ', ' + (EX + 30) + ' ' + (r.y - 40) + ', ' + (MX + NW / 2 + 4) + ' ' + (r.y - 8), 'loop');
        if (x.loopLabel) addLabel(EX, x.y + 42, x.loopLabel, '', 'middle');
      }
    });

    const all = [];
    function select(elm, item) {
      all.forEach((e) => e.classList.remove('on'));
      elm.classList.add('on');
      panel.innerHTML = '<span class="gtag">' + (item.gate ? 'GATE' : item.cls ? 'EXIT' : 'STAGE') + '</span>' +
        '<h4>' + item.label + '</h4>' +
        '<p>' + (item.info || '') + '</p>' +
        (item.writes && item.writes.length ? '<div class="writes">' + item.writes.map((w) => '<span class="badge neutral">' + w + '</span>').join('') + '</div>' : '');
    }
    N.forEach((n) => {
      const g = document.createElementNS(NS, 'g');
      if (n.gate) {
        g.setAttribute('class', 'gdiamond');
        g.innerHTML = '<path d="M ' + MX + ' ' + (n.y - 26) + ' L ' + (MX + 78) + ' ' + n.y + ' L ' + MX + ' ' + (n.y + 26) + ' L ' + (MX - 78) + ' ' + n.y + ' Z"/>' +
          '<text x="' + MX + '" y="' + (n.y + 3.5) + '" text-anchor="middle">' + n.label + '</text>';
      } else {
        g.setAttribute('class', 'gnode');
        g.innerHTML = '<rect x="' + (MX - NW / 2) + '" y="' + (n.y - NH / 2) + '" width="' + NW + '" height="' + NH + '" rx="9"/>' +
          '<text x="' + (MX - NW / 2 + 14) + '" y="' + (n.y - 2) + '">' + n.label + '</text>' +
          '<text class="sub" x="' + (MX - NW / 2 + 14) + '" y="' + (n.y + 13) + '">' + (n.sub || '') + '</text>';
      }
      g.addEventListener('mouseenter', () => select(g, n));
      g.addEventListener('click', () => select(g, n));
      svg.appendChild(g); all.push(g);
    });
    (graph.exits || []).forEach((x) => {
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'gexit ' + x.cls);
      g.innerHTML = '<rect x="' + (EX - EW / 2) + '" y="' + (x.y - 20) + '" width="' + EW + '" height="40" rx="9"/>' +
        '<text x="' + (EX - EW / 2 + 12) + '" y="' + (x.y - 2) + '">' + x.label + '</text>' +
        '<text class="sub" x="' + (EX - EW / 2 + 12) + '" y="' + (x.y + 12) + '">' + (x.sub || '') + '</text>';
      g.addEventListener('mouseenter', () => select(g, x));
      g.addEventListener('click', () => select(g, x));
      svg.appendChild(g); all.push(g);
    });
    select(all[0], N[0]);
  }
})();
