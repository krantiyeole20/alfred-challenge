/**
 * alfred_ demo — Gemini key proxy.
 *
 * The page is static and runs the database in WASM, so this Worker exists for
 * exactly one reason: an API key cannot ship in a static site. It relays model
 * turns and nothing else.
 *
 * It deliberately never sees the mailbox data. Tool calls are executed by the
 * browser against its own local SQLite copy, and only the results come back
 * through here as ordinary conversation content. That keeps the freeform SQL
 * tool safe by construction — there is no server-side database to attack.
 *
 * Two independent limits protect the bill:
 *   per-IP    stops one visitor hammering the demo
 *   global    a hard daily ceiling; this is the one that actually caps spend
 */

const ALLOWED_ORIGINS = [
  "https://krantiyeole20.github.io",
  "http://localhost:8788",
  "http://127.0.0.1:8788",
];

// Models the client is allowed to ask for. Without this the proxy is an open
// relay to any model on the account, at any price.
const ALLOWED_MODELS = new Set([
  "gemini-3.5-flash-lite",
  "gemini-3.6-flash",
]);

const DEFAULT_MODEL = "gemini-3.5-flash-lite";

const LIMITS = {
  perIpPerHour: 40,
  globalPerDay: 1500,
  maxBodyBytes: 512 * 1024,
  maxOutputTokens: 2048,
  maxTurns: 40,
};

function cors(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...cors(origin) },
  });
}

/** Day and hour buckets in UTC, used as counter keys. */
function buckets(now = new Date()) {
  const day = now.toISOString().slice(0, 10);
  const hour = now.toISOString().slice(0, 13);
  return { day, hour };
}

/**
 * Count this request against both limits.
 *
 * Uses D1 rather than the Rate Limiting binding for the global cap: the
 * binding is per-location and explicitly documented as "not an accurate
 * accounting system", which is fine for stopping abuse but useless as a
 * spend ceiling. A counter row is exact.
 */
async function checkAndCount(db, ip, { day, hour }) {
  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS usage (
         bucket TEXT PRIMARY KEY,
         n INTEGER NOT NULL DEFAULT 0,
         updated_at TEXT NOT NULL
       )`
    )
    .run();

  const globalKey = `global:${day}`;
  const ipKey = `ip:${ip}:${hour}`;
  const nowIso = new Date().toISOString();

  const rows = await db
    .prepare(`SELECT bucket, n FROM usage WHERE bucket IN (?, ?)`)
    .bind(globalKey, ipKey)
    .all();

  let globalN = 0;
  let ipN = 0;
  for (const r of rows.results ?? []) {
    if (r.bucket === globalKey) globalN = r.n;
    if (r.bucket === ipKey) ipN = r.n;
  }

  if (globalN >= LIMITS.globalPerDay) {
    return {
      ok: false,
      status: 429,
      error:
        "The shared demo budget for today is used up. It resets at 00:00 UTC — " +
        "this cap is what keeps the demo free to run.",
    };
  }
  if (ipN >= LIMITS.perIpPerHour) {
    return {
      ok: false,
      status: 429,
      error: `Rate limit: ${LIMITS.perIpPerHour} questions per hour per visitor. Try again shortly.`,
    };
  }

  await db.batch([
    db
      .prepare(
        `INSERT INTO usage (bucket, n, updated_at) VALUES (?, 1, ?)
         ON CONFLICT(bucket) DO UPDATE SET n = n + 1, updated_at = excluded.updated_at`
      )
      .bind(globalKey, nowIso),
    db
      .prepare(
        `INSERT INTO usage (bucket, n, updated_at) VALUES (?, 1, ?)
         ON CONFLICT(bucket) DO UPDATE SET n = n + 1, updated_at = excluded.updated_at`
      )
      .bind(ipKey, nowIso),
  ]);

  return {
    ok: true,
    remaining: {
      today: LIMITS.globalPerDay - globalN - 1,
      thisHour: LIMITS.perIpPerHour - ipN - 1,
    },
  };
}

/** Reject anything that isn't the conversation shape we expect. */
function validate(payload) {
  if (!payload || typeof payload !== "object") return "body must be a JSON object";
  if (!Array.isArray(payload.contents)) return "contents must be an array";
  if (payload.contents.length === 0) return "contents is empty";
  if (payload.contents.length > LIMITS.maxTurns)
    return `conversation too long (max ${LIMITS.maxTurns} turns)`;
  if (payload.model && !ALLOWED_MODELS.has(payload.model))
    return `model not allowed; choose one of: ${[...ALLOWED_MODELS].join(", ")}`;
  return null;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") ?? "";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(origin) });
    }

    const url = new URL(request.url);

    if (url.pathname === "/api/health") {
      return json({ ok: true, models: [...ALLOWED_MODELS] }, 200, origin);
    }

    if (request.method !== "POST" || url.pathname !== "/api/chat") {
      return json({ error: "POST /api/chat" }, 404, origin);
    }

    if (origin && !ALLOWED_ORIGINS.includes(origin)) {
      return json({ error: "origin not allowed" }, 403, origin);
    }

    const raw = await request.text();
    if (raw.length > LIMITS.maxBodyBytes) {
      return json({ error: "request too large" }, 413, origin);
    }

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return json({ error: "invalid JSON" }, 400, origin);
    }

    const invalid = validate(payload);
    if (invalid) return json({ error: invalid }, 400, origin);

    const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const gate = await checkAndCount(env.DB, ip, buckets());
    if (!gate.ok) return json({ error: gate.error }, gate.status, origin);

    const model = payload.model ?? DEFAULT_MODEL;
    const body = {
      contents: payload.contents,
      generationConfig: {
        temperature: 0,
        maxOutputTokens: LIMITS.maxOutputTokens,
      },
    };
    if (payload.systemInstruction) body.systemInstruction = payload.systemInstruction;
    if (payload.tools) body.tools = payload.tools;

    const upstream = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-goog-api-key": env.GEMINI_API_KEY,
        },
        body: JSON.stringify(body),
      }
    );

    if (!upstream.ok) {
      const detail = await upstream.text();
      // Pass the status through so the client can distinguish "slow down"
      // from "that request was malformed", but never leak the key or headers.
      return json(
        { error: "upstream model error", status: upstream.status, detail: detail.slice(0, 500) },
        upstream.status === 429 ? 429 : 502,
        origin
      );
    }

    const data = await upstream.json();
    return json({ ...data, _remaining: gate.remaining }, 200, origin);
  },
};
