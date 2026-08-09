# Runbook — add the `/review` redirect to `openmasorah-site`

**Target repo:** `/Users/benlamm/Workspace/masorah/openmasorah-site` (NOT writable from the
masorah-review session — this file exists so Ben or a session invoked in that repo applies it).

## Why

`masorah-review` is deployed at `https://review.openmasorah.com` (Lightsail, see
`state.json` in this directory). Ben asked for it to be reachable as a "subpage of
openmasorah.com". The app hardcodes root-absolute URLs (`/login`, `/api/*`, `/static/*`,
`/admin`, `/expert`, `/auth/*`) in server redirects and in eight files under `static/`,
and has no `root_path` support — so serving it *at* `openmasorah.com/review/*` would be a
code change, not a config change. Ben chose the subdomain + redirect option.

## The change

`openmasorah-site/netlify.toml` currently has no `[[redirects]]` block. Append:

```toml
[[redirects]]
  from = "/review/*"
  to = "https://review.openmasorah.com/:splat"
  status = 302
  force = true

[[redirects]]
  from = "/review"
  to = "https://review.openmasorah.com/"
  status = 302
  force = true
```

Both rules are needed: Netlify's `/review/*` splat does **not** match the bare `/review`
path, so without the second rule `openmasorah.com/review` 404s on the marketing site.

`force = true` makes the rule win even if a `dist/review*` asset ever exists. `302` (not
`200`) is deliberate — a `200` would make it a *proxy*, which would put the review app's
session cookie on the `openmasorah.com` apex, shared with the marketing site, and would
require proxying `/login`, `/api/*`, `/static/*`, `/admin`, `/expert` and `/auth/*` at the
apex root as well. A `302` hands the browser off to the subdomain, where the app already
works unmodified.

## Verify after deploy

```bash
curl -sI https://openmasorah.com/review        | grep -iE '^(HTTP|location)'
curl -sI https://openmasorah.com/review/login  | grep -iE '^(HTTP|location)'
```

Expect `302` and a `location:` of `https://review.openmasorah.com/` and
`https://review.openmasorah.com/login` respectively.

Note the apex already 301s to `www.openmasorah.com`, so the first hop may be
`openmasorah.com -> www.openmasorah.com/review -> review.openmasorah.com/`. Confirm the
chain end-to-end with `curl -sIL` and check the final URL, not just the first response.

## What NOT to touch

- Do not change `[build]` or `[build.environment]` in `netlify.toml`.
- Do not add a `status = 200` proxy variant — see the cookie-scope reasoning above.
- Do not point the redirect at the Lightsail IP or the interim `sslip.io` host; use the
  `review.openmasorah.com` name so the certificate matches.
