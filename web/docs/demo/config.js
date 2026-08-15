// Deployment config for the demo.
//
// WORKER_URL is the Cloudflare Worker that holds the Gemini key. Until it is
// deployed the page still works completely for browsing, search and the six
// deterministic questions — only the conversational agent needs the proxy.
// That split is deliberate: the parts that prove the design are the parts
// that never needed a server.

export const CONFIG = {
  WORKER_URL: "https://alfred-demo-proxy.workers.dev",
  DB_URL: "data/alfred.db.gz",
  QUESTIONS_URL: "data/questions.json",
  MODEL: "gemini-3.5-flash-lite",

  // The corpus is frozen. "Overdue" and "stale" are relative to this instant,
  // not to the visitor's clock, or every answer would drift over time.
  AS_OF: "2026-08-12T23:59:59",
};
