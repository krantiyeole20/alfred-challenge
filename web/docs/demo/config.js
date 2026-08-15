// Deployment config for the demo.
//
// The agent needs a proxy because an API key cannot ship in a static page.
// Which proxy depends on where the page is being served from:
//
//   localhost  ->  tools/serve_demo.py, same origin, no CORS, no account
//   Pages      ->  the Cloudflare Worker in worker/
//
// Everything else — the six questions, search, the evidence drawer — runs
// entirely in the tab and needs no proxy at all.

const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);

export const CONFIG = {
  // Same origin locally; the deployed Worker otherwise. Replace the Worker
  // URL with your own after `npx wrangler deploy`.
  WORKER_URL: LOCAL ? "" : "https://alfred-demo-proxy.krantiyeole20.workers.dev",

  DB_URL: "data/alfred.db.gz",
  QUESTIONS_URL: "data/questions.json",
  MODEL: "gemini-3.5-flash-lite",

  // The corpus is frozen. "Overdue" and "stale" are relative to this instant,
  // not to the visitor's clock, or every answer would drift over time.
  AS_OF: "2026-08-12T23:59:59",
};
