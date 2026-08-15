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

// Words that match nearly every email and only dilute a search.
const STOPWORDS = new Set([
  "the", "a", "an", "any", "anything", "some", "from", "for", "of", "to", "in",
  "on", "do", "did", "does", "have", "has", "had", "is", "are", "was", "were",
  "me", "my", "i", "we", "you", "your", "about", "with", "and", "or", "what",
  "who", "when", "where", "which", "there", "this", "that", "get", "got",
]);

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

  /**
   * Search the mailbox.
   *
   * LIKE over a precomputed lowercase blob rather than FTS5: the sql.js WASM
   * build has no FTS5 module, so a virtual table would throw "no such module"
   * on every call. Over 1,500 rows this is a few milliseconds.
   *
   * Terms are ANDed, then if that finds nothing the search falls back to ORing
   * them -- "Marcus Bell" should still find Marcus when only the first name
   * appears, rather than returning nothing and stalling the agent.
   */
  search(userId, query, limit = 15) {
    const terms = String(query ?? "")
      .toLowerCase()
      .replace(/[^\w@.\- ]+/g, " ")
      .split(/\s+/)
      .filter((t) => t.length > 1 && !STOPWORDS.has(t));
    if (!terms.length) return [];

    const run = (joiner) => {
      const where = terms.map((_, i) => `s.blob LIKE :t${i}`).join(joiner);
      const params = { ":u": userId, ":n": limit };
      terms.forEach((t, i) => (params[`:t${i}`] = `%${t}%`));
      return this.all(
        `SELECT e.provider_message_id AS message_id, e.subject, e.thread_id,
                e.provider_ts AS sent_at, e.direction, s.sender,
                substr(e.body_text_novel, 1, 260) AS excerpt
         FROM email_search s JOIN emails e ON e.id = s.email_id
         WHERE s.user_id = :u AND (${where})
         ORDER BY e.provider_ts DESC LIMIT :n`,
        params
      );
    };

    const strict = run(" AND ");
    return strict.length ? strict : run(" OR ");
  }

  /**
   * Mail involving one person, by name or address.
   *
   * "Do I have anything from Marcus?" is a participant question, not a
   * full-text one -- it was the gap that sent the agent into a run_sql loop.
   */
  fromPerson(userId, who, limit = 15) {
    const needle = `%${String(who ?? "").toLowerCase().trim()}%`;
    if (needle.length < 4) return { matches: [], messages: [] };

    const matches = this.all(
      `SELECT DISTINCT p.canonical_name AS name, pi.address
       FROM people p JOIN person_identities pi ON pi.person_id = p.id
       WHERE p.user_id = :u
         AND (lower(p.canonical_name) LIKE :q OR lower(pi.address) LIKE :q)
       LIMIT 8`,
      { ":u": userId, ":q": needle }
    );

    const messages = this.all(
      `SELECT e.provider_message_id AS message_id, e.subject, e.thread_id,
              e.provider_ts AS sent_at, e.direction,
              pt.role, pt.raw_name AS person, pt.raw_address AS address
       FROM email_participants pt JOIN emails e ON e.id = pt.email_id
       WHERE e.user_id = :u
         AND (lower(pt.raw_name) LIKE :q OR lower(pt.raw_address) LIKE :q)
       ORDER BY e.provider_ts DESC LIMIT :n`,
      { ":u": userId, ":q": needle, ":n": limit }
    );
    return { matches, messages };
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
