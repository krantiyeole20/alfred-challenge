// ============================================================================
// Doc widgets: SQL syntax highlighting + the ordering-rule demo.
// ============================================================================

(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';

  /* ------------------------------------------------- SQL highlighting */
  const KW = /\b(CREATE|TABLE|VIEW|INDEX|UNIQUE|EXTENSION|IF|NOT|EXISTS|NULL|DEFAULT|PRIMARY|KEY|REFERENCES|ON|DELETE|CASCADE|CONSTRAINT|FOREIGN|ALTER|ADD|SELECT|DISTINCT|FROM|JOIN|LEFT|WHERE|AND|OR|ORDER|BY|DESC|ASC|NULLS|LAST|AS|IS|IN|LIKE)\b/g;
  const TYPES = /\b(UUID|TEXT|BOOLEAN|SMALLINT|INTEGER|BIGINT|NUMERIC|TIMESTAMPTZ|JSONB)\b/g;

  window.highlightSQL = function () {
    document.querySelectorAll('pre.sql').forEach((pre) => {
      let s = pre.textContent;
      s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const holes = [];
      s = s.replace(/--[^\n]*/g, (m) => { holes.push('<span class="c">' + m + '</span>'); return '@@H' + (holes.length - 1) + 'H@@'; });
      s = s.replace(/'[^']*'/g, (m) => { holes.push('<span class="s">' + m + '</span>'); return '@@H' + (holes.length - 1) + 'H@@'; });
      s = s.replace(KW, '<span class="k">$&</span>');
      s = s.replace(TYPES, '<span class="t">$&</span>');
      s = s.replace(/@@H(\d+)H@@/g, (m, i) => holes[+i]);
      pre.innerHTML = s;
    });
  };

  /* ------------------------------------------------- ordering demo */
  const EVID = [
    { id: 'E1', val: 'Jun 20, 2025', sentX: 90, obsX: 760, sent: 'sent Jun 10, 2025', obs: 'processed TODAY (backfill)' },
    { id: 'E2', val: 'Aug 8, 2026', sentX: 540, obsX: 300, sent: 'sent Jul 30, 2026', obs: 'processed Jul 30' },
    { id: 'E3', val: 'Aug 15, 2026', sentX: 740, obsX: 540, sent: 'sent Aug 12, 2026', obs: 'processed Aug 12' },
  ];

  window.renderOrderingDemo = function (root) {
    root.classList.add('clock');
    root.innerHTML =
      '<div class="clock-controls"><span class="lbl">Fold “latest wins” by:</span>' +
      '<button class="mode-btn" data-m="obs">observed_at — when we learned it</button>' +
      '<button class="mode-btn" data-m="prov">provider_ts — when it was sent</button></div>' +
      '<div class="clock-svg"></div>' +
      '<div class="clock-result"><div class="result-box"></div><p class="clock-note"></p></div>';

    const W = 880, H = 290, yS = 70, yO = 210;
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    root.querySelector('.clock-svg').appendChild(svg);

    let parts = '';
    parts += '<line class="axis-line" x1="60" y1="' + yS + '" x2="' + (W - 100) + '" y2="' + yS + '"/>';
    parts += '<line class="axis-line" x1="60" y1="' + yO + '" x2="' + (W - 100) + '" y2="' + yO + '"/>';
    parts += '<text class="axis-name" x="60" y="' + (yS - 34) + '">provider_ts — when the email was sent</text>';
    parts += '<text class="axis-name" x="60" y="' + (yO + 44) + '">observed_at — when our pipeline processed it</text>';
    parts += '<text class="axis-lab" x="' + (W - 92) + '" y="' + (yS + 4) + '">later →</text>';
    parts += '<text class="axis-lab" x="' + (W - 92) + '" y="' + (yO + 4) + '">later →</text>';
    EVID.forEach((e) => {
      parts += '<line class="ev-link" x1="' + e.sentX + '" y1="' + (yS + 7) + '" x2="' + e.obsX + '" y2="' + (yO - 7) + '"/>';
    });
    EVID.forEach((e) => {
      parts += '<circle class="ev-dot" data-id="' + e.id + '" data-axis="sent" cx="' + e.sentX + '" cy="' + yS + '" r="7"/>';
      parts += '<text class="ev-lab" x="' + e.sentX + '" y="' + (yS - 16) + '" text-anchor="middle">' + e.id + ' · “' + e.val + '”</text>';
      parts += '<text class="ev-val" x="' + e.sentX + '" y="' + (yS + 24) + '" text-anchor="middle">' + e.sent + '</text>';
      parts += '<circle class="ev-dot" data-id="' + e.id + '" data-axis="obs" cx="' + e.obsX + '" cy="' + yO + '" r="7"/>';
      parts += '<text class="ev-lab" x="' + e.obsX + '" y="' + (yO - 16) + '" text-anchor="middle">' + e.id + '</text>';
      parts += '<text class="ev-val" x="' + e.obsX + '" y="' + (yO + 24) + '" text-anchor="middle">' + e.obs + '</text>';
    });
    parts += '<circle class="win-ring" r="13" cx="-99" cy="-99"/>';
    svg.innerHTML = parts;

    const bObs = root.querySelector('[data-m="obs"]'), bProv = root.querySelector('[data-m="prov"]');
    const resEl = root.querySelector('.result-box'), noteEl = root.querySelector('.clock-note');
    function setMode(mode) {
      bObs.classList.toggle('sel-obs', mode === 'obs');
      bProv.classList.toggle('sel-prov', mode === 'prov');
      svg.querySelectorAll('.ev-dot').forEach((d) => d.classList.remove('win'));
      root.classList.remove('win-ok', 'win-bad');
      const winner = mode === 'obs' ? EVID[0] : EVID[2];
      const axis = mode === 'obs' ? 'obs' : 'sent';
      const dot = svg.querySelector('.ev-dot[data-id="' + winner.id + '"][data-axis="' + axis + '"]');
      dot.classList.add('win');
      const ring = svg.querySelector('.win-ring');
      ring.setAttribute('cx', dot.getAttribute('cx')); ring.setAttribute('cy', dot.getAttribute('cy'));
      if (mode === 'obs') {
        root.classList.add('win-bad');
        resEl.className = 'result-box evil';
        resEl.innerHTML = 'work_items.due_at = <b>Jun 20, 2025</b> ✗<small>a 14-month-old deadline just overwrote the current one</small>';
        noteEl.innerHTML = 'Backfill runs newest-first, so the oldest email is processed <i>last</i> — today. It has the newest <code>observed_at</code> and wins a fold it should lose. The hazard exists only during backfill, which is exactly why it survives testing and breaks in production.';
      } else {
        root.classList.add('win-ok');
        resEl.className = 'result-box good';
        resEl.innerHTML = 'work_items.due_at = <b>Aug 15, 2026</b> ✓<small>the most recently <i>sent</i> claim wins, whatever order we process in</small>';
        noteEl.innerHTML = 'Ordering by <code>provider_ts</code> makes the fold <b>order-independent</b>: process history forwards, backwards, or twice — the projection converges to the same value. <code>observed_at</code> only breaks ties.';
      }
    }
    bObs.addEventListener('click', () => setMode('obs'));
    bProv.addEventListener('click', () => setMode('prov'));
    setMode('obs');
  };
})();
