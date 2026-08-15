/**
 * The database, in the tab.
 *
 * Fetches a 2 MB gzipped SQLite file once, decompresses it with the platform's
 * own DecompressionStream, and runs it in WASM. From then on every question is
 * a local query — no round trip, no cold start, nothing to keep alive.
 *
 * The six question SQL statements are NOT written here. They are loaded from
 * data/questions.json, which is generated from src/pipeline/questions.py, the
 * same file the scorer runs. Hand-copying them into JavaScript is exactly how
 * a demo quietly stops matching the thing that was measured.
 */

import { CONFIG } from "./config.js";

export class Ledger {
  constructor() {
    this.db = null;
    this.questions = {};
  }

  async open(onProgress = () => {}) {
    onProgress("loading engine", 10);
    const SQL = await initSqlJs({
      locateFile: (f) => `https://cdn.jsdelivr.net/npm/sql.js@1.11.0/dist/${f}`,
    });

    onProgress("fetching ledger", 35);
    const res = await fetch(CONFIG.DB_URL);
    if (!res.ok) throw new Error(`could not fetch ${CONFIG.DB_URL} (${res.status})`);

    onProgress("decompressing", 62);
    // DecompressionStream is in every current browser; this avoids depending on
    // the host to negotiate Content-Encoding for an octet-stream.
    const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
    const buf = await new Response(stream).arrayBuffer();

    onProgress("opening", 84);
    this.db = new SQL.Database(new Uint8Array(buf));

    const qres = await fetch(CONFIG.QUESTIONS_URL);
    this.questions = qres.ok ? await qres.json() : {};

    onProgress("ready", 100);
    return this;
  }

  /** Run SQL and return plain row objects. */
  all(sql, params = {}) {
    const stmt = this.db.prepare(sql);
    try {
      stmt.bind(params);
      const out = [];
      while (stmt.step()) out.push(stmt.getAsObject());
      return out;
    } finally {
      stmt.free();
    }
  }

  one(sql, params = {}) {
    return this.all(sql, params)[0] ?? null;
  }

  // ── reference data ────────────────────────────────────────────────

  accounts() {
    return this.all(`
      SELECT a.provider_account_id AS profile_id,
             a.email_address       AS address,
             u.id                  AS user_id,
             u.display_name        AS name,
             u.role_profile        AS role,
             u.timezone            AS timezone,
             (SELECT count(*) FROM emails e WHERE e.user_id = u.id) AS mail_count
      FROM accounts a JOIN users u ON u.id = a.user_id
      ORDER BY u.display_name
    `);
  }

  stats(userId) {
    return this.one(
      `SELECT
         (SELECT count(*) FROM emails       WHERE user_id = :u) AS emails,
         (SELECT count(*) FROM threads      WHERE user_id = :u) AS threads,
         (SELECT count(*) FROM people       WHERE user_id = :u) AS people,
         (SELECT count(*) FROM evidence     WHERE user_id = :u) AS evidence,
         (SELECT count(*) FROM work_items   WHERE user_id = :u AND status = 'OPEN') AS open_items,
         (SELECT count(*) FROM identity_conflicts WHERE user_id = :u) AS conflicts`,
      { ":u": userId }
    );
  }

  // ── the six ───────────────────────────────────────────────────────

  ask(key, userId, limit = 12) {
    const q = this.questions[key];
    if (!q) throw new Error(`unknown question: ${key}`);
    return this.all(q.sql, {
      ":user_id": userId,
      ":limit": limit,
      ":as_of": CONFIG.AS_OF,
      ":window_days": 14,
    });
  }

  // ── agent tools ───────────────────────────────────────────────────

  /** Full-text search over the mailbox. */
  search(userId, query, limit = 15) {
    // FTS5 treats punctuation as syntax; quote each term so a user typing
    // an address or "Q3 - deck" doesn't produce a syntax error.
    const safe = String(query)
      .split(/\s+/)
      .filter(Boolean)
      .map((t) => `"${t.replace(/"/g, '""')}"`)
      .join(" ");
    if (!safe) return [];
    return this.all(
      `SELECT e.provider_message_id AS message_id,
              e.subject, e.provider_ts AS sent_at, e.direction,
              substr(e.body_text_novel, 1, 260) AS excerpt,
              (SELECT raw_address FROM email_participants p
                WHERE p.email_id = e.id AND p.role='from' LIMIT 1) AS sender
       FROM email_fts f
       JOIN emails e ON e.id = f.email_id
       WHERE email_fts MATCH :q AND e.user_id = :u
       ORDER BY bm25(email_fts) LIMIT :n`,
      { ":q": safe, ":u": userId, ":n": limit }
    );
  }

  /** One message, with its participants and attachments. */
  message(messageId) {
    const m = this.one(
      `SELECT e.*, t.normalized_subject FROM emails e
       LEFT JOIN threads t ON t.id = e.thread_id
       WHERE e.provider_message_id = :m`,
      { ":m": messageId }
    );
    if (!m) return null;
    m.participants = this.all(
      `SELECT role, raw_name, raw_address, is_user FROM email_participants
       WHERE email_id = :e ORDER BY role, position`,
      { ":e": m.id }
    );
    return m;
  }

  /** Every message in a thread, oldest first. */
  thread(threadId) {
    return this.all(
      `SELECT e.provider_message_id AS message_id, e.subject, e.provider_ts AS sent_at,
              e.direction, e.body_text_novel AS body,
              (SELECT raw_address FROM email_participants p
                WHERE p.email_id = e.id AND p.role='from' LIMIT 1) AS sender
       FROM emails e WHERE e.thread_id = :t ORDER BY e.provider_ts`,
      { ":t": threadId }
    );
  }

  /** Suspected impersonation: same display name, confusingly similar domain. */
  conflicts(userId) {
    return this.all(
      `SELECT observed_name, address_a, address_b, relation,
              message_count_a, message_count_b
       FROM identity_conflicts WHERE user_id = :u
       ORDER BY CASE relation
                  WHEN 'embedded_brand' THEN 1
                  WHEN 'typosquat' THEN 2 ELSE 3 END`,
      { ":u": userId }
    );
  }

  /**
   * The freeform escape hatch.
   *
   * Safe by construction rather than by validation: this is a disposable copy
   * of a public fixture living in the visitor's own tab. There is no server,
   * no other tenant, and nothing to corrupt — reloading restores it. The
   * guards below are about keeping the agent honest and the UI responsive,
   * not about defending data.
   */
  sql(userId, statement) {
    const s = String(statement).trim().replace(/;+\s*$/, "");
    if (!/^\s*(select|with)\b/i.test(s)) {
      return { error: "Only SELECT / WITH queries are allowed." };
    }
    if (/;/.test(s)) {
      return { error: "One statement at a time." };
    }
    try {
      const rows = this.all(s.includes(":user_id") ? s : s, { ":user_id": userId });
      return { rows: rows.slice(0, 50), row_count: rows.length };
    } catch (err) {
      return { error: String(err.message ?? err) };
    }
  }

  schema() {
    return this.all(
      `SELECT name, sql FROM sqlite_master
       WHERE type='table' AND name NOT LIKE 'sqlite_%'
         AND name NOT LIKE 'email_fts%' ORDER BY name`
    );
  }
}
