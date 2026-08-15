/**
 * UI wiring.
 *
 * The six questions render as deterministic tables straight from SQLite — no
 * model involved, no network. The composer routes to the agent, which is the
 * only thing that needs the proxy. If the proxy is down or the daily cap is
 * spent, everything except the composer still works, which is the point.
 */

import { CONFIG } from "./config.js";
import { Ledger } from "./db.js";
import { Agent } from "./agent.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const AS_OF = new Date(CONFIG.AS_OF);

const state = {
  ledger: null,
  agent: null,
  account: null,
  accounts: [],
  busy: false,
};

// ── boot ────────────────────────────────────────────────────────────

async function boot() {
  const status = $("bootStatus");
  const bar = $("bootBar");
  try {
    state.ledger = await new Ledger().open((msg, pct) => {
      status.textContent = msg;
      bar.style.width = `${pct}%`;
    });
  } catch (err) {
    status.textContent = "could not open the ledger";
    status.style.color = "var(--bad)";
    $("boot").querySelector(".boot-note").textContent = String(err.message ?? err);
    return;
  }

  state.agent = new Agent(state.ledger);
  state.accounts = state.ledger.accounts();

  renderAccounts();
  renderQuestions();
  selectAccount(state.accounts[0]);

  $("asof").textContent = AS_OF.toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  });

  $("boot").classList.add("done");
  $("shell").hidden = false;
  wire();
}

// ── rail ────────────────────────────────────────────────────────────

function renderAccounts() {
  const box = $("accounts");
  box.replaceChildren();
  for (const a of state.accounts) {
    const b = el("button", "acct");
    b.append(el("span", "acct-name", a.name));
    b.append(el("span", "acct-role", `${a.role.replace(/_/g, " ")} · ${a.mail_count}`));
    b.onclick = () => selectAccount(a);
    b.dataset.id = a.user_id;
    box.append(b);
  }
}

function renderQuestions() {
  const box = $("queries");
  box.replaceChildren();
  const qs = state.ledger.questions;
  let i = 1;
  for (const [key, q] of Object.entries(qs)) {
    const b = el("button", "q");
    b.append(el("span", "q-num", String(i++).padStart(2, "0")));
    b.append(el("span", "q-text", q.label));
    b.dataset.key = key;
    b.onclick = () => showQuestion(key);
    box.append(b);
  }
}

function selectAccount(a) {
  state.account = a;
  state.agent.reset(a.user_id);
  for (const b of document.querySelectorAll(".acct")) {
    b.classList.toggle("on", b.dataset.id === a.user_id);
  }
  for (const b of document.querySelectorAll(".q")) b.classList.remove("on");

  $("who").innerHTML = "";
  $("who").append(el("b", null, a.name), el("span", "who-addr", a.address));
  $("ask").placeholder = `Ask about ${a.name.split(" ")[0]}'s mailbox…`;

  renderIntro();
}

// ── intro ───────────────────────────────────────────────────────────

function renderIntro() {
  const s = state.ledger.stats(state.account.user_id);
  const stage = $("stage");
  stage.replaceChildren();

  const intro = el("div", "intro");
  intro.append(el("span", "doc-kicker", "live demo · evidence ledger"));
  intro.append(
    el("h1", null, "Six questions an inbox can't answer by searching.")
  );
  intro.append(
    el("p", "lede",
      "Every claim here was extracted from a single email, checked verbatim " +
      "against its source, and folded into current state by plain code. Pick a " +
      "question on the left, or ask in your own words. Pull on any citation and " +
      "the message it came from opens, with the quote highlighted where it was found.")
  );

  const stats = el("div", "intro-stats");
  const pairs = [
    [s.emails, "messages"],
    [s.threads, "threads"],
    [s.people, "people"],
    [s.evidence, "claims"],
    [s.open_items, "open items"],
  ];
  if (s.conflicts) pairs.push([s.conflicts, "impersonations"]);
  for (const [n, label] of pairs) {
    const d = el("div", "stat");
    d.append(el("div", "stat-n", Number(n).toLocaleString()));
    d.append(el("div", "stat-l", label));
    stats.append(d);
  }
  intro.append(stats);
  stage.append(intro);

  if (!s.evidence) {
    const note = el("div", "note");
    note.style.marginTop = "26px";
    note.textContent =
      "No claims extracted yet. Run `python -m src.pipeline.run extract` and " +
      "`export-web` to populate the ledger — mail browsing and search work regardless.";
    stage.append(note);
  }
}

// ── the six ─────────────────────────────────────────────────────────

function showQuestion(key) {
  for (const b of document.querySelectorAll(".q")) {
    b.classList.toggle("on", b.dataset.key === key);
  }
  const q = state.ledger.questions[key];
  const rows = state.ledger.ask(key, state.account.user_id, 12);

  const stage = $("stage");
  stage.replaceChildren();

  const wrap = el("div", "answer");
  const h2 = el("h2");
  const idx = Object.keys(state.ledger.questions).indexOf(key) + 1;
  h2.append(el("span", "snum", String(idx).padStart(2, "0")));
  h2.append(document.createTextNode(q.label));
  wrap.append(h2);
  wrap.append(el("p", "answer-why", q.why));

  if (!rows.length) {
    const e = el("div", "empty");
    e.innerHTML =
      "<b>Nothing matches.</b><br>Either the ledger has no evidence yet, or this " +
      "genuinely has no open items — which is itself an answer.";
    wrap.append(e);
  } else {
    wrap.append(renderRows(rows, key));
  }

  wrap.append(renderHow(q));
  stage.append(wrap);
  wrap.scrollIntoView({ block: "start", behavior: "smooth" });
}

/**
 * "How this is answered" — the designed job and dataflow behind the question.
 *
 * This is the part that distinguishes a ledger from a search box: the answer
 * above was produced by a named pipeline with a named trigger, and every stage
 * of it is inspectable. The SQL shown is the SQL that ran.
 */
function renderHow(q) {
  const d = el("details", "how");
  d.append(el("summary", null, "How this answer is produced"));
  const body = el("div", "how-body");

  if (q.trigger) {
    const t = el("p", "how-trigger");
    t.append(el("b", null, "Trigger"));
    t.append(document.createTextNode(q.trigger));
    body.append(t);
  }

  if (q.pipeline?.length) {
    const flow = el("div", "flow");
    q.pipeline.forEach(([name, desc, writes], i) => {
      const step = el("div", "flow-step");
      if (name === "Read") step.classList.add("is-read");
      step.append(el("div", "flow-n", String(i + 1).padStart(2, "0")));
      step.append(el("div", "flow-name", name));
      const col = el("div", "flow-desc", desc);
      if (writes) col.append(el("span", "flow-writes", `writes ${writes}`));
      step.append(col);
      flow.append(step);
    });
    body.append(flow);
  }

  if (q.reads?.length) {
    body.append(el("p", "how-reads", `reads · ${q.reads.join(" · ")}`));
  }

  const sql = el("pre", "how-sql", (q.sql || "").trim().replace(/\n\s{8}/g, "\n"));
  body.append(sql);

  d.append(body);
  return d;
}

function renderRows(rows, key) {
  const box = el("div", "rows");
  rows.forEach((r, i) => {
    const row = el("div", "row");
    if (i < 3) row.classList.add("is-top");
    row.style.animationDelay = `${i * 42}ms`;

    row.append(el("div", "row-rank", String(i + 1).padStart(2, "0")));

    const main = el("div", "row-main");
    main.append(el("div", "row-title", r.title ?? "(untitled)"));

    const meta = el("div", "row-meta");
    if (key === "q5_what_changed" && r.change_type) {
      const closed = /resolv|complet|clos|cancel/.test(r.change_type);
      meta.append(el("span", `tag ${closed ? "ok" : ""}`, r.change_type.replace(/_/g, " ")));
      if (r.changed_at) meta.append(el("span", "tag", fmtDate(r.changed_at)));
    }
    if (r.speech_act) meta.append(el("span", "tag", r.speech_act));
    if (r.owner_is_self) meta.append(el("span", "tag self", "yours"));
    else if (r.owner_name) meta.append(el("span", "tag", r.owner_name));
    if (r.due_at) {
      const over = new Date(r.due_at) < AS_OF;
      meta.append(el("span", `tag ${over ? "over" : "due"}`,
        `${over ? "overdue" : "due"} ${fmtDate(r.due_at)}`));
    }
    if (r.last_activity_at) {
      const days = Math.round((AS_OF - new Date(r.last_activity_at)) / 864e5);
      if (days >= 7) meta.append(el("span", "tag", `quiet ${days}d`));
    }
    main.append(meta);

    if (r.evidence_quote) {
      const cite = el("button", "cite");
      cite.append(el("span", "cite-q", `\u201C${r.evidence_quote}\u201D`));
      cite.append(el("span", "cite-open", "open source message"));
      cite.onclick = () => openSource(r.anchor_message_id, r.evidence_quote);
      main.append(cite);
    }
    row.append(main);
    box.append(row);
  });
  return box;
}

// ── evidence drawer ─────────────────────────────────────────────────

function openSource(messageId, quote) {
  const m = messageId && state.ledger.message(messageId);
  const body = $("drawerBody");
  body.replaceChildren();

  if (!m) {
    body.append(el("p", "empty", "Source message not found."));
  } else {
    body.append(el("div", "msg-subject", m.subject ?? "(no subject)"));

    const head = el("div", "msg-head");
    const from = m.participants.find((p) => p.role === "from");
    const to = m.participants.filter((p) => p.role === "to");
    const cc = m.participants.filter((p) => p.role === "cc");
    head.innerHTML =
      `<b>from</b>  ${esc(from ? `${from.raw_name ?? ""} <${from.raw_address}>` : "—")}<br>` +
      `<b>to</b>    ${esc(to.map((p) => p.raw_address).join(", ") || "—")}<br>` +
      (cc.length ? `<b>cc</b>    ${esc(cc.map((p) => p.raw_address).join(", "))}<br>` : "") +
      `<b>date</b>  ${esc(fmtDateTime(m.provider_ts))}<br>` +
      `<b>id</b>    ${esc(m.provider_message_id)}`;
    body.append(head);

    // The quote is highlighted where it actually appears. If it isn't found,
    // that itself is worth seeing — it would mean the citation was fabricated,
    // which the pipeline's quote check is supposed to make impossible.
    const text = m.body_text_full || m.body_text_novel || "";
    body.append(highlight(text, quote));

    const atts = safeJson(m.attachments, []);
    for (const a of atts) {
      if (!a.extracted_text) continue;
      const box = el("div", "msg-att");
      box.append(el("div", "msg-att-name", `attachment · ${a.filename}`));
      box.append(highlight(a.extracted_text, quote));
      body.append(box);
    }
  }

  const d = $("drawer");
  d.classList.add("open");
  d.setAttribute("aria-hidden", "false");
}

function highlight(text, quote) {
  const pre = el("div", "msg-body");
  const idx = quote ? text.indexOf(quote) : -1;
  if (idx < 0) {
    pre.textContent = text;
    return pre;
  }
  pre.append(document.createTextNode(text.slice(0, idx)));
  pre.append(el("mark", null, quote));
  pre.append(document.createTextNode(text.slice(idx + quote.length)));
  return pre;
}

function closeDrawer() {
  const d = $("drawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
}

// ── agent ───────────────────────────────────────────────────────────

async function askAgent(question) {
  if (state.busy || !question.trim()) return;
  state.busy = true;
  $("send").disabled = true;

  const stage = $("stage");
  const turn = el("div", "turn");
  const you = el("div", "turn-you");
  you.append(el("b", null, "you "), document.createTextNode(" " + question));
  turn.append(you);

  const trace = el("div", "tool-trace");
  turn.append(trace);

  const thinking = el("div", "thinking", "thinking");
  turn.append(thinking);
  stage.append(turn);
  turn.scrollIntoView({ block: "start", behavior: "smooth" });

  try {
    const { text, citations, remaining } = await state.agent.ask(question, {
      onTool: (name) => {
        const t = el("span", "trace");
        t.append(el("b", null, name));
        trace.append(t);
        // trace grows in place; no scroll needed
      },
    });

    thinking.remove();
    const say = el("div", "turn-say");
    for (const para of text.split(/\n{2,}/)) {
      if (para.trim()) say.append(el("p", null, para.trim()));
    }
    turn.append(say);

    // Citations are rendered by the interface from tool results, never from
    // model text — so a quote on screen is always one the pipeline verified.
    const seen = new Set();
    const cited = citations.filter((c) => {
      if (!c.evidence_quote || seen.has(c.work_item_id)) return false;
      seen.add(c.work_item_id);
      return true;
    });
    if (cited.length) turn.append(renderRows(cited.slice(0, 6), "agent"));

    if (remaining?.today != null) {
      $("composerNote").textContent =
        `${remaining.today} questions left in today's shared demo budget.`;
    }
  } catch (err) {
    thinking.remove();
    const note = el("div", "note warn");
    note.textContent = friendlyError(err);
    turn.append(note);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    turn.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function friendlyError(err) {
  const msg = String(err.message ?? err);
  if (/failed to fetch|networkerror/i.test(msg)) {
    return (
      "Can't reach the model proxy. The six questions on the left still work — " +
      "they run entirely in this tab and never needed a server."
    );
  }
  return msg;
}

// ── schema peek ─────────────────────────────────────────────────────

function showSchema() {
  const stage = $("stage");
  stage.replaceChildren();
  const wrap = el("div", "answer");
  wrap.append(el("h2", null, "The ledger"));
  wrap.append(
    el("p", "answer-why",
      "This is the read path, running in your browser. The full design is 35 " +
      "tables; the demo ships the ones that answer questions.")
  );
  // One collapsible block per table rather than 6,000px of raw dump.
  for (const t of state.ledger.schema()) {
    const d = el("details", "how");
    d.style.marginTop = "0";
    const rows = state.ledger.one(
      `SELECT count(*) n FROM ${t.name}`
    );
    const sum = el("summary", null, `${t.name}  ·  ${Number(rows?.n ?? 0).toLocaleString()} rows`);
    d.append(sum);
    const pre = el("pre", "how-sql", t.sql);
    pre.style.marginTop = "12px";
    d.append(pre);
    wrap.append(d);
  }
  stage.append(wrap);
  wrap.scrollIntoView({ block: "start", behavior: "smooth" });
}

// ── wiring ──────────────────────────────────────────────────────────

function wire() {
  $("send").onclick = () => {
    const v = $("ask").value;
    $("ask").value = "";
    askAgent(v);
  };
  $("ask").onkeydown = (e) => {
    if (e.key === "Enter") $("send").click();
  };
  $("drawerClose").onclick = closeDrawer;

  // On narrow screens the rail is a collapsible header; choosing from it must
  // close it, otherwise the answer renders below the fold and nothing moves.
  const collapseRail = () => {
    const nav = document.querySelector(".side-nav");
    if (nav && window.matchMedia("(max-width: 1000px)").matches) nav.open = false;
  };
  document.querySelector(".side").addEventListener("click", (e) => {
    if (e.target.closest(".q, .acct")) collapseRail();
  });
  $("btnSchema").onclick = showSchema;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
}

// ── helpers ─────────────────────────────────────────────────────────

const fmtDate = (iso) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

const fmtDateTime = (iso) =>
  new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit",
  });

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function safeJson(s, fallback) {
  try {
    return JSON.parse(s) ?? fallback;
  } catch {
    return fallback;
  }
}

boot();
