# Demo proxy

A Cloudflare Worker that exists for one reason: an API key cannot ship in a
static site. It relays model turns and nothing else.

It never sees the mailbox data. The page runs SQLite in WASM, executes every
tool call locally, and sends only the results back through here as ordinary
conversation content. That is also why the agent's freeform SQL tool is safe:
there is no server-side database to attack, only a disposable copy in the
visitor's own tab.

## Deploy

```bash
cd worker
npm install -g wrangler          # if you don't have it

npx wrangler d1 create alfred-demo
#  -> paste the printed database_id into wrangler.toml

npx wrangler secret put GEMINI_API_KEY
#  -> paste the key when prompted; it is never written to disk

npx wrangler deploy
```

Then put the deployed URL into `web/app/config.js` as `WORKER_URL`.

## Limits

Both are enforced in `src/index.js`:

| Limit | Default | Purpose |
|---|---|---|
| per IP, per hour | 40 | stops one visitor hammering the demo |
| global, per day | 1500 | the hard spend ceiling |

The global cap is the one that actually protects the bill. When it trips the
demo returns a clear "budget used up, resets at 00:00 UTC" message rather than
failing obscurely.

Only `gemini-3.5-flash-lite` and `gemini-3.6-flash` are accepted. Without that
allowlist the proxy would be an open relay to any model on the account, at any
price.

## Cost

Everything here is inside Cloudflare's free tier: Workers 100k requests/day,
D1 5 GB storage with 5M row reads/day. At the 1500/day cap the only real cost
is Gemini tokens — roughly a cent per question at 3.5-flash-lite.

## Local development

```bash
npx wrangler dev --local
```

Serves on `http://localhost:8787`. The local origins are already in the CORS
allowlist.
