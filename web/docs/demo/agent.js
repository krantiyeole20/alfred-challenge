/**
 * The agent loop.
 *
 * Model turns go through the Worker (which holds the key). Tool calls come
 * back and are executed HERE, against the local SQLite copy, and only the
 * results are sent onward. The Worker never sees the mailbox.
 *
 * Six of the tools are the fixed questions — identical SQL to what the scorer
 * ran, so what the agent answers is what was measured. The seventh is a
 * freeform read-only query for everything outside the six.
 */

import { CONFIG } from "./config.js";

// A failing tool used to burn the whole budget in retries; 12 leaves room
// to recover and still bounds a runaway loop.
export const MAX_HOPS = 12;

export const TOOLS = [
  {
    name: "needs_attention",
    description:
      "Rank what most needs the owner's attention right now. Use for 'what should I look at', " +
      "'what's urgent', 'catch me up'. Returns open work items ordered by a score over urgency, " +
      "importance, staleness and commitment.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "forgetting",
    description:
      "Promises the owner made that have gone quiet with nothing recorded as closing them. " +
      "Use for 'what am I forgetting', 'did I drop anything', 'what have I not done'.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "waiting_on_me",
    description:
      "Open items the owner owns: requests addressed to them plus commitments they made. " +
      "Use for 'what's on my plate', 'what do I owe people', 'what's blocked on me'.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "waiting_on_others",
    description:
      "Open items someone else owns, that nothing has closed. Use for 'what am I waiting on', " +
      "'who owes me', 'what's blocked on other people', 'who hasn't replied'.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "what_changed",
    description:
      "Things that moved recently: deadlines shifted, owners reassigned, items resolved or " +
      "cancelled, values superseded. Use for 'what changed', 'what's new', 'what moved'.",
    parameters: {
      type: "object",
      properties: {
        window_days: { type: "number", description: "Look back this many days. Default 14." },
      },
      required: [],
    },
  },
  {
    name: "slipping",
    description:
      "Open items that have gone unusually quiet, are past their date, or were never claimed " +
      "by anyone. Use for 'what's slipping', 'what's falling through the cracks', 'what's stale'.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "search_mail",
    description:
      "Full-text search across this mailbox. Use when the question names a person, company, " +
      "project, invoice number or topic that the six ranked questions would not surface.",
    parameters: {
      type: "object",
      properties: { query: { type: "string", description: "Search terms." } },
      required: ["query"],
    },
  },
  {
    name: "mail_from_person",
    description:
      "Find mail involving a specific person, by name or email address, and the " +
      "addresses that person uses. Use this FIRST for any question naming a person " +
      "— 'anything from Marcus', 'what did Dana say', 'has legal replied'. This is " +
      "a participant lookup; search_mail only matches text.",
    parameters: {
      type: "object",
      properties: {
        who: { type: "string", description: "A person's name or email address." },
      },
      required: ["who"],
    },
  },
  {
    name: "read_thread",
    description:
      "Read every message in one thread, oldest first. Use after search_mail when you need the " +
      "full exchange to answer accurately rather than guessing from an excerpt.",
    parameters: {
      type: "object",
      properties: { thread_id: { type: "string" } },
      required: ["thread_id"],
    },
  },
  {
    name: "suspected_impersonation",
    description:
      "Senders using a real counterparty's display name from a confusingly similar domain — " +
      "invoice fraud and phishing. Use for 'is anything suspicious', 'any fraud', 'scam'.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "run_sql",
    description:
      "Run a read-only SELECT against the ledger for questions the other tools do not cover — " +
      "counts, groupings, cross-cutting joins. Tables: emails, threads, people, " +
      "person_identities, email_participants, email_signals, evidence, work_items, " +
      "work_item_changes, attention_candidates, identity_conflicts. Always filter by " +
      "user_id = :user_id. Call get_schema first if unsure of a column.",
    parameters: {
      type: "object",
      properties: { sql: { type: "string", description: "A single SELECT statement." } },
      required: ["sql"],
    },
  },
  {
    name: "get_schema",
    description: "Return CREATE TABLE statements for the ledger. Use before writing run_sql.",
    parameters: { type: "object", properties: {}, required: [] },
  },
];

const SYSTEM = `You answer questions about one person's mailbox, using tools that query a \
ledger built from their email.

The ledger already did the hard part. Claims were extracted from individual messages, each \
checked verbatim against its source, then folded into current state by deterministic code. \
Your job is to route to the right tool, read what comes back, and say what it means.

Rules:
- Always call a tool before answering a question about the mailbox. Never answer from memory \
or invent an item; if a tool returns nothing, say so plainly.
- Prefer the six ranked questions over run_sql. They encode the definitions that were \
measured against a gold set; ad-hoc SQL does not.
- Lead with the answer. Two or three sentences of substance, then stop. This is a busy \
person catching up, not a report.
- Name people and dates concretely. "Dana is waiting on the DPA addendum, promised Aug 5" \
beats "there are some outstanding items".
- Never print a work_item_id, user_id or raw UUID. They mean nothing to the reader.
- Do not invent quotes. The interface renders citations from the tool results itself.
- If something looks like fraud, say so directly and say what not to do about it.
- The corpus is frozen at ${CONFIG.AS_OF}. "Today" means that date, not the real one.
- You can see exactly ONE mailbox: the one named below. If the question names a
different person as the mailbox owner, say plainly that you are looking at this
mailbox and they can switch accounts in the left rail — do not answer as though
you had the other person's mail.`;

/** The owner is per-session, so it rides on the system prompt, not the tools. */
function systemFor(owner) {
  return owner
    ? `${SYSTEM}\n\nThe mailbox you are reading belongs to ${owner.name} <${owner.address}>, ${owner.role.replace(/_/g, " ")}.`
    : SYSTEM;
}

export class Agent {
  constructor(ledger) {
    this.ledger = ledger;
    this.contents = [];
    this.userId = null;
  }

  reset(userId, owner = null) {
    this.userId = userId;
    this.owner = owner;
    this.contents = [];
    this.trace = [];
  }

  /** Everything the last turns did, for the UI and for debugging. */
  telemetry() {
    return this.trace;
  }

  /** Execute a tool call locally. Returns { result, rows } — rows drive citations. */
  run(name, args = {}) {
    const u = this.userId;
    const L = this.ledger;
    const six = {
      needs_attention: "q1_needs_attention",
      forgetting: "q2_forgetting",
      waiting_on_me: "q3_waiting_on_me",
      waiting_on_others: "q4_waiting_on_others",
      what_changed: "q5_what_changed",
      slipping: "q6_slipping_through_cracks",
    };

    if (six[name]) {
      const rows = L.ask(six[name], u, 12);
      return { rows, result: rows.map(slim) };
    }
    if (name === "search_mail") {
      const rows = L.search(u, args.query ?? "", 12);
      return {
        rows: [],
        result: rows.length
          ? rows
          : {
              matches: 0,
              hint:
                "No text match. If the question names a person, call " +
                "mail_from_person instead — it searches participants, not body text.",
            },
      };
    }
    if (name === "mail_from_person") {
      const found = L.fromPerson(u, args.who ?? "", 12);
      return {
        rows: [],
        result: found.messages.length
          ? found
          : {
              matches: 0,
              hint: `Nobody matching "${args.who}" appears in this mailbox. Say so plainly rather than guessing.`,
            },
      };
    }
    if (name === "read_thread") {
      return { rows: [], result: L.thread(args.thread_id) };
    }
    if (name === "suspected_impersonation") {
      return { rows: [], result: L.conflicts(u) };
    }
    if (name === "run_sql") {
      const out = L.sql(u, args.sql ?? "");
      if (!out.error && out.row_count === 0) {
        out.hint =
          "Zero rows. Do not retry variations blindly — prefer mail_from_person " +
          "for people, search_mail for topics, or one of the six ranked questions.";
      }
      return { rows: [], result: out };
    }
    if (name === "get_schema") {
      return { rows: [], result: L.schema() };
    }
    return { rows: [], result: { error: `unknown tool ${name}` } };
  }

  /**
   * One user turn, streamed.
   *
   * Emits as it goes so the page can render tokens the moment they arrive:
   *   onText(delta)   a chunk of the answer
   *   onTool(name)    a tool call starting
   *   onToolDone(n)   that tool returned n rows
   * Loops until the model stops asking for tools.
   */
  async ask(question, { onText = () => {}, onTool = () => {}, onToolDone = () => {} } = {}) {
    this.contents.push({ role: "user", parts: [{ text: question }] });
    const citations = [];
    let remaining = null;

    for (let hop = 0; hop < MAX_HOPS; hop++) {
      const res = await fetch(`${CONFIG.WORKER_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: CONFIG.MODEL,
          systemInstruction: { parts: [{ text: systemFor(this.owner) }] },
          contents: this.contents,
          tools: [{ functionDeclarations: TOOLS }],
        }),
      });

      const today = res.headers.get("X-Alfred-Remaining-Today");
      if (today != null) remaining = { today: Number(today) };

      if (!res.ok) {
        let detail = `proxy error ${res.status}`;
        try {
          detail = (await res.json()).error ?? detail;
        } catch { /* non-JSON error body */ }
        throw new Error(detail);
      }

      // Gemini's SSE: one `data: {...}` per chunk, each a partial
      // GenerateContentResponse. Text arrives incrementally; a functionCall
      // part arrives whole.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const parts = [];
      let textPart = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === "[DONE]") continue;

          let chunk;
          try {
            chunk = JSON.parse(payload);
          } catch {
            continue;
          }
          for (const part of chunk.candidates?.[0]?.content?.parts ?? []) {
            if (part.text) {
              onText(part.text);
              if (textPart) textPart.text += part.text;
              else parts.push((textPart = { text: part.text }));
            } else if (part.functionCall) {
              parts.push(part);
            }
          }
        }
      }

      const calls = parts.filter((p) => p.functionCall);
      if (!calls.length) {
        const text = parts.map((p) => p.text ?? "").join("").trim();
        this.contents.push({ role: "model", parts: parts.length ? parts : [{ text }] });
        return { text, citations, remaining };
      }

      this.contents.push({ role: "model", parts });

      const responses = [];
      for (const { functionCall } of calls) {
        const { name, args } = functionCall;
        onTool(name);
        let out;
        try {
          out = this.run(name, args ?? {});
        } catch (err) {
          out = { rows: [], result: { error: String(err.message ?? err) } };
        }
        const n = Array.isArray(out.result)
          ? out.result.length
          : out.result?.messages?.length ?? out.result?.row_count ?? out.rows.length;
        const failed = Boolean(out.result?.error) || n === 0;
        const entry = {
          hop, name, args, rows: n, failed,
          error: out.result?.error ?? null,
          hint: out.result?.hint ?? null,
          at: new Date().toISOString(),
        };
        this.trace.push(entry);
        // Surfaced in the console too: a silent tool failure is what turned
        // one broken search into a six-hop stall.
        console.debug("[alfred tool]", entry);
        onToolDone(name, n, failed);
        citations.push(...out.rows);
        responses.push({ functionResponse: { name, response: { result: out.result } } });
      }
      this.contents.push({ role: "user", parts: responses });
    }

    const tried = this.trace.slice(-MAX_HOPS).map((t) => t.name).join(", ");
    return {
      text:
        "I couldn't settle that within the tool budget for one question. " +
        `Tools tried: ${tried}. Try naming the person or topic more specifically, ` +
        "or pick one of the six questions on the left.",
      citations,
      remaining,
    };
  }
}

/** Trim a work-item row to what the model needs; UUIDs are noise to it. */
function slim(r) {
  return {
    title: r.title,
    speech_act: r.speech_act,
    status: r.status,
    due_at: r.due_at,
    owned_by_owner: !!r.owner_is_self,
    owner: r.owner_name,
    requester: r.requester_name,
    last_activity: r.last_activity_at,
    subject: r.subject,
    quote: r.evidence_quote,
    change_type: r.change_type,
    changed_at: r.changed_at,
  };
}
