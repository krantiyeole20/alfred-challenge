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
  inChat: false,
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
  setComposer(false);
  renderGuide();

  $("asof").textContent = AS_OF.toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  });

  $("boot").classList.add("done");
  $("shell").hidden = false;
  wire();
  applyDeepLink();
}

/**
 * ?mailbox=finance&q=q2_forgetting
 *
 * Makes a particular answer linkable, which is what you want when sending
 * someone "look at this one" — and lets the screenshots in the README be
 * generated from the real page rather than staged by hand.
 */
function applyDeepLink() {
  const params = new URLSearchParams(location.search);
  const mailbox = params.get("mailbox");
  if (mailbox) {
    const found = state.accounts.find(
      (a) => a.profile_id === mailbox || a.name.toLowerCase().startsWith(mailbox.toLowerCase())
    );
    if (found) selectAccount(found);
  }
  // ?ask=... runs a question on load, so a link can carry the whole
  // demonstration rather than instructions for reproducing it.
  const ask = params.get("ask");
  if (ask && state.account) {
    $("ask").value = "";
    askAgent(ask);
    return;
  }

  const q = params.get("q");
  if (q && state.account && state.ledger.questions[q]) {
    showQuestion(q, false, false);
    // Deep-link straight into a panel, so a link can point at the exact thing
    // being discussed rather than "the demo, go find it".
    const open = params.get("open");
    if (open === "how") document.querySelector(".action-btn")?.click();
    else if (open === "cite") document.querySelector(".cite")?.click();
  }
}

// ── rail ────────────────────────────────────────────────────────────

function renderAccounts() {
  const box = $("accounts");
  box.replaceChildren();
  for (const a of state.accounts) {
    const b = el("button", "acct");
    b.append(el("span", "acct-name", a.name));
    b.append(el("span", "acct-role", `${a.role.replace(/_/g, " ")} · ${a.mail_count}`));
    // Single click selects. Double-click on the ACTIVE mailbox clears it and
    // returns to the guide -- discoverable only once you are already there,
    // which is why the active row also carries a visible hint.
    b.onclick = () => selectAccount(a);
    b.ondblclick = () => {
      if (state.account?.user_id === a.user_id) deselectAccount();
    };
    b.title = "Click to open · double-click to close";
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
    b.onclick = () => {
      if (!state.account) return flashRail();
      showQuestion(key);
    };
    box.append(b);
  }
}

function selectAccount(a) {
  state.account = a;
  state.agent.reset(a.user_id, a);
  for (const b of document.querySelectorAll(".acct")) {
    b.classList.toggle("on", b.dataset.id === a.user_id);
  }
  for (const b of document.querySelectorAll(".q")) b.classList.remove("on");

  $("who").innerHTML = "";
  $("who").append(el("b", null, a.name), el("span", "who-addr", a.address));
  $("ask").placeholder = `Ask about ${a.name.split(" ")[0]}'s mailbox…`;
  setComposer(true);
  renderIntro();
}

/** Back to the guide. The agent has no mailbox to answer about, so it stops. */
function deselectAccount() {
  state.account = null;
  state.inChat = false;
  for (const b of document.querySelectorAll(".acct")) b.classList.remove("on");
  for (const b of document.querySelectorAll(".q")) b.classList.remove("on");
  $("who").replaceChildren();
  setComposer(false);
  const u = new URL(location.href);
  u.search = "";
  history.replaceState(null, "", u);
  renderGuide();
}

/**
 * The composer is only live once a mailbox is chosen.
 *
 * A chat box that accepts typing and then has nothing to answer about is
 * worse than one that plainly says what it is waiting for.
 */
function setComposer(on) {
  $("composer").classList.toggle("armed", on);
  $("ask").disabled = !on;
  $("send").disabled = !on;
  if (!on) {
    $("ask").value = "";
    $("ask").placeholder = "Pick a mailbox to ask about…";
    $("composerNote").textContent = "";
  }
}

// ── guide ───────────────────────────────────────────────────────────

/**
 * The guide. What a first-time visitor sees before choosing anything.
 *
 * Three things have to land in a few seconds: pick a mailbox, then either
 * take one of the six or ask in your own words. The fourth -- that those six
 * ARE the agent's tools, one per question, running the same SQL the scorer
 * is measured against -- is the actual thesis, so it gets its own line
 * rather than a footnote.
 *
 * The arrows are drawn by hand rather than set in type because they are
 * scaffolding: annotation over an interface, the kind you stop seeing once
 * you know where things are.
 */
function renderGuide() {
  state.inChat = false;
  const stage = $("stage");
  stage.replaceChildren();

  const g = el("div", "guide");
  g.append(el("span", "doc-kicker", "live demo \u00b7 evidence ledger"));
  g.append(el("h1", "guide-title", "Start by picking a mailbox."));
  g.append(
    el("p", "guide-sub",
      "Five people, 300 messages each, frozen at Aug 12 2026. Everything here runs " +
      "in this tab against a 2.6 MB copy of the database \u2014 your questions go to the " +
      "model, but the mail never leaves your browser.")
  );

  const steps = el("div", "guide-steps");
  const STEPS = [
    ["01", "Pick a mailbox", "Five on the left. Each is a different job, so each has a different kind of mess.", "left"],
    ["02", "Take one of the six", "The questions an inbox can\u2019t answer by searching. Answers come back ranked, every line carrying the sentence it came from.", "left"],
    ["03", "Or just ask", "Plain language, in the bar at the bottom. \u201cWhat am I forgetting?\u201d \u00b7 \u201canything from Marcus?\u201d \u00b7 \u201cis any of this a scam?\u201d", "down"],
  ];
  for (const [n, title, body, dir] of STEPS) {
    const step = el("div", `guide-step to-${dir}`);
    step.append(el("div", "guide-n", n));
    const col = el("div", "guide-step-body");
    col.append(el("div", "guide-step-title", title));
    col.append(el("div", "guide-step-text", body));
    step.append(col);
    step.append(handArrow(dir));
    steps.append(step);
  }
  g.append(steps);

  const note = el("div", "guide-note");
  note.append(el("b", null, "The six questions are the agent\u2019s tools."));
  note.append(document.createTextNode(
    " Not instructions in a prompt \u2014 one tool per question, each running the same " +
    "SQL the scorer is measured against. Ask in your own words and the model picks " +
    "between them, but the answer is still a query over the ledger rather than " +
    "something generated. Open \u201cHow this answer is produced\u201d on any answer to " +
    "see the job behind it."
  ));
  g.append(note);

  stage.append(g);
}

/** A slightly loose hand-drawn arrow. Two directions is all the guide needs. */
function handArrow(dir) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `guide-arrow arrow-${dir}`);
  svg.setAttribute("viewBox", dir === "down" ? "0 0 64 92" : "0 0 150 64");
  svg.setAttribute("aria-hidden", "true");
  const paths =
    dir === "down"
      // curls down and left, toward the composer
      ? ["M46 5c5 22 3 39-8 51-7 8-16 13-25 16",
         "M13 72c-1-6-1-11-1-16", "M13 72c5-2 10-4 14-7"]
      // sweeps left toward the mailbox rail
      : ["M144 10c-30 4-55 11-76 21-14 7-26 15-41 24",
         "M27 55c-1-7-1-13 0-19", "M27 55c6-1 12-3 17-5"];
  for (const d of paths) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

/** Nudge the rail when someone picks a question with no mailbox open. */
function flashRail() {
  const rail = $("accounts");
  rail.classList.remove("nudge");
  void rail.offsetWidth;
  rail.classList.add("nudge");
}


// ── intro ───────────────────────────────────────────────────────────

function renderIntro() {
  state.inChat = false;
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

function showQuestion(key, push = true, scroll = true) {
  state.inChat = false;
  if (push) {
    const u = new URL(location.href);
    u.searchParams.set("mailbox", state.account.profile_id);
    u.searchParams.set("q", key);
    history.replaceState(null, "", u);
  }
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

  // The job behind the answer gets a first-class control, not a footnote:
  // same gesture as opening a source message, opened in the same drawer.
  const actions = el("div", "answer-actions");
  const howBtn = el("button", "action-btn");
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("width", "14");
  icon.setAttribute("height", "14");
  icon.setAttribute("fill", "none");
  icon.setAttribute("stroke", "currentColor");
  icon.setAttribute("stroke-width", "2");
  icon.setAttribute("stroke-linecap", "round");
  icon.setAttribute("stroke-linejoin", "round");
  icon.classList.add("action-icon");
  // a stack: the pipeline layers this answer came through
  for (const d of ["M3 7h18", "M3 12h18", "M3 17h18"]) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    icon.append(path);
  }
  howBtn.append(icon);
  howBtn.append(el("span", null, "How this answer is produced"));
  howBtn.append(el("span", "action-hint", `${(q.pipeline || []).length} stages \u00b7 ${q.trigger ? q.trigger.split(" \u2014 ")[0].toLowerCase() : "job"}`));
  howBtn.onclick = () => openHow(q, idx);
  actions.append(howBtn);
  wrap.append(actions);

  if (!rows.length) {
    const e = el("div", "empty");
    e.innerHTML =
      "<b>Nothing matches.</b><br>Either the ledger has no evidence yet, or this " +
      "genuinely has no open items — which is itself an answer.";
    wrap.append(e);
  } else {
    wrap.append(renderRows(rows, key));
  }

  stage.append(wrap);
  if (scroll) wrap.scrollIntoView({ block: "start", behavior: "smooth" });
}

/**
 * "How this answer is produced" — the designed job and dataflow, opened in the
 * evidence drawer.
 *
 * This is what separates a ledger from a search box: the answer was produced by
 * a named job with a named trigger, and every stage of it is inspectable down
 * to the SQL that ran. It gets the same affordance as opening a source message
 * because it is the same kind of act — following a claim back to its origin.
 */
function openHow(q, idx) {
  const body = $("drawerBody");
  body.replaceChildren();
  $("drawerTitle").textContent = "How it works";

  body.append(el("div", "msg-subject", q.label));
  body.append(el("p", "how-lede", q.why));

  if (q.trigger) {
    const t = el("div", "how-trigger");
    t.append(el("b", null, "Trigger"));
    t.append(document.createTextNode(q.trigger));
    body.append(t);
  }

  if (q.pipeline?.length) {
    body.append(el("p", "side-label", "Pipeline"));
    const flow = el("div", "flow");
    q.pipeline.forEach(([name, desc, writes], i) => {
      const step = el("div", "flow-step");
      if (name === "Read") step.classList.add("is-read");
      step.append(el("div", "flow-n", String(i + 1).padStart(2, "0")));
      const col = el("div", "flow-body");
      col.append(el("div", "flow-name", name));
      col.append(el("div", "flow-desc", desc));
      if (writes) col.append(el("span", "flow-writes", `writes ${writes}`));
      step.append(col);
      flow.append(step);
    });
    body.append(flow);
  }

  if (q.reads?.length) {
    body.append(el("p", "side-label", "Tables read"));
    const list = el("div", "how-reads");
    for (const t of q.reads) list.append(el("code", null, t));
    body.append(list);
  }

  body.append(el("p", "side-label", "The query that ran"));
  body.append(el("pre", "how-sql", (q.sql || "").trim().replace(/\n\s{8}/g, "\n")));

  openDrawer();
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

  $("drawerTitle").textContent = "Source message";
  openDrawer();
}

function openDrawer() {
  const d = $("drawer");
  d.classList.add("open");
  d.setAttribute("aria-hidden", "false");
  $("drawerClose").focus();
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
  if (!state.account) return flashRail();
  state.busy = true;
  $("send").disabled = true;

  // The transcript persists: a chat you cannot scroll back through is a
  // search box that forgets. Questions append rather than replace.
  const stage = $("stage");
  if (!state.inChat) {
    stage.replaceChildren();
    state.inChat = true;
    for (const b of document.querySelectorAll(".q")) b.classList.remove("on");
  }

  const turn = el("div", "turn");
  const you = el("div", "turn-you");
  you.append(el("b", null, "You asked"));
  you.append(el("span", "turn-you-text", question));
  turn.append(you);

  const reply = el("div", "turn-reply");
  const trace = el("div", "tool-trace");
  reply.append(trace);

  const status = el("div", "thinking", "reading the ledger");
  reply.append(status);

  const say = el("div", "turn-say");
  reply.append(say);
  turn.append(reply);
  stage.append(turn);
  turn.scrollIntoView({ block: "start", behavior: "smooth" });

  let streamed = "";
  const chips = new Map();

  try {
    const { text, citations, remaining } = await state.agent.ask(question, {
      onTool: (name) => {
        status.textContent = "querying the ledger";
        const chip = el("span", "trace running");
        chip.append(el("b", null, name.replace(/_/g, " ")));
        chips.set(`${name}:${chips.size}`, chip);
        state.lastChip = chip;
        trace.append(chip);
      },
      onToolDone: (name, n, failed) => {
        const chip = state.lastChip;
        if (!chip) return;
        chip.classList.remove("running");
        chip.classList.add(failed ? "failed" : "done");
        chip.append(el("span", "trace-n", n === 1 ? "1 row" : `${n} rows`));
      },
      onText: (delta) => {
        status.remove();
        streamed += delta;
        say.textContent = streamed;
        // Follow the text only while the reader is already at the bottom, so
        // scrolling back mid-answer is not yanked forward.
        const nearBottom =
          window.innerHeight + window.scrollY >= document.body.scrollHeight - 140;
        if (nearBottom) say.scrollIntoView({ block: "end", behavior: "smooth" });
      },
    });

    status.remove();
    // Re-render once complete; streaming used a single text node so partial
    // output never reflows into half-formed blocks mid-answer.
    say.classList.add("done");
    renderMarkdown(say, text || streamed);

    // Citations are rendered by the interface from tool results, never parsed
    // out of model text — so a quote on screen is always one the pipeline
    // verified against its source.
    const seen = new Set();
    const cited = citations.filter((c) => {
      if (!c.evidence_quote || seen.has(c.work_item_id)) return false;
      seen.add(c.work_item_id);
      return true;
    });
    if (cited.length) {
      reply.append(el("p", "cited-label", "Evidence for this answer"));
      reply.append(renderRows(cited.slice(0, 5), "agent"));
    }

    if (remaining?.today != null) {
      $("composerNote").textContent =
        `${remaining.today} questions left in today's shared demo budget.`;
    }
  } catch (err) {
    status.remove();
    const note = el("div", "note warn");
    note.textContent = friendlyError(err);
    reply.append(note);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    $("ask").focus();
  }
}


/**
 * Minimal markdown for model output: paragraphs, bullets, bold, inline code.
 *
 * Deliberately not a markdown library — the model emits a narrow subset, and
 * everything is inserted as text nodes rather than innerHTML, so nothing in a
 * model response can inject markup into the page.
 */
function renderMarkdown(target, src) {
  target.replaceChildren();
  const lines = String(src ?? "").split("\n");
  let list = null;

  const inline = (node, text) => {
    // **bold** and `code`, applied as real elements, never as HTML.
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) node.append(text.slice(last, m.index));
      const tok = m[0];
      node.append(
        tok.startsWith("**")
          ? el("strong", null, tok.slice(2, -2))
          : el("code", null, tok.slice(1, -1))
      );
      last = m.index + tok.length;
    }
    if (last < text.length) node.append(text.slice(last));
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    const bullet = line.match(/^[-*\u2022]\s+(.*)$/);
    if (bullet) {
      if (!list) target.append((list = el("ul", "md-list")));
      const li = el("li");
      inline(li, bullet[1]);
      list.append(li);
    } else {
      list = null;
      const p = el("p");
      inline(p, line);
      target.append(p);
    }
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
  state.inChat = false;
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
  //
  // Desktop has to be forced back open rather than merely left alone: a
  // <details> that gets collapsed while narrow stays collapsed after the
  // window widens, and the summary that would reopen it is display:none at
  // that width — so the entire rail disappears with no way back.
  const narrow = window.matchMedia("(max-width: 1000px)");
  const nav = document.querySelector(".side-nav");
  const syncRail = () => {
    if (nav && !narrow.matches) nav.open = true;
  };
  syncRail();
  narrow.addEventListener("change", syncRail);

  const collapseRail = () => {
    if (nav && narrow.matches) nav.open = false;
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
