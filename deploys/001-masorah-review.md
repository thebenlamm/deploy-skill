# 001 — masorah-review

**Date:** 2026-08-09 · **Status:** LIVE at https://review.openmasorah.com — 3 items open (see bottom)
**Repo:** `/Users/benlamm/Workspace/masorah/masorah-review` @ `f510105` (main, clean tree)
**Stack:** FastAPI + uvicorn (Python 3.12, `uv`), external Postgres (hosted Supabase), static HTML/JS
**Target:** Lightsail `micro_3_0` (1GB), us-east-1a, profile `openmasorah` (acct 338375260556)
**Cost:** ~$7/mo instance + static IP (free while attached)
**IP:** 44.207.43.91 · **Host:** `review.openmasorah.com` (Route 53 A record, TTL 300)
**Interim host used during build:** `44-207-43-91.sslip.io` (Caddy later repointed at the real name)

## Gates cleared

| Gate | Answer |
|------|--------|
| URL shape | Subdomain `review.openmasorah.com` + a 302 from `openmasorah.com/review` (NOT a true path — see below) |
| Spend | Lightsail micro ~$7/mo on the `openmasorah` profile |
| Access | Public login page; invite still required to get in |
| Branch | `main` @ `f510105`, clean tree, analyzer found no unresolved imports |

## Friction log

### 1. `curl -s ifconfig.me` returns IPv6 → Lightsail port API rejects it — MECHANIZABLE
`put-instance-public-ports` requires an IPv4 CIDR. On an IPv6-capable network
`ifconfig.me` answers with the v6 address, and the call fails:

```
InvalidInputException: "2600:4040:...:dd6/32" is not a valid IPv4 CIDR format
```

**Fix:** `curl -4 -s ifconfig.me`. `provisioning.md` §2 should carry the `-4`.
This will bite every operator on a dual-stack home connection.

### 2. zsh does not word-split unquoted variables — MECHANIZABLE
The docs' implicit `SSH="ssh -i key ..."` then `$SSH 'cmd'` idiom is bash-only. Under
the user's zsh it fails with `no such file or directory: ssh -i /path/... ubuntu@ip`
(the whole string treated as one command name). **Fix:** write a small executable
helper script (`mrssh`) in the scratchpad and call that, or use `${=SSH}`.
Worth stating in `build-recipes.md` since the platform line says `Shell: zsh`.

### 3. `ProtectHome=true` breaks asyncpg SSL DSN parsing — MECHANIZABLE, HIGH VALUE
With `ProtectHome=true` in the systemd unit, asyncpg's connect path stats
`~/.postgresql/postgresql.key` and gets `PermissionError: [Errno 13]` instead of a
clean "not found". That is an `OSError`, so the app's `except SQLAlchemyError` misses
it and a readiness probe 500s. **This would have broken the real Supabase connection
too** — it is hit during every SSL DSN parse, not just when the DB is down.

**Fix:** `ProtectHome=tmpfs` (not `true`). `/home` becomes an empty tmpfs, so the stat
returns ENOENT and asyncpg correctly concludes "no client cert". Hardening preserved.
Any Postgres app using asyncpg/psycopg over SSL hits this — belongs in the
`provisioning.md` §3 unit template with the reasoning inline.

### 4. Certificate Transparency scanners arrive in ~90 seconds — CONFIRMS THE SKILL
Concrete evidence for the privacy gate: within 90s of Caddy getting the Let's Encrypt
cert, and before the URL was shared with anyone, scanners from 91.148.244.131 and
158.69.55.82 were probing `/user_secrets.yml`, `/.env.production`, `/server.key`,
`/backup.sql`, `/.npmrc`. All returned 404 (verified). Good material for the gate's
"not sharing the URL is not privacy" wording — it is observed, not theoretical.

### 5. openmasorah.com DNS zone was not in the obvious account — NOT MECHANIZABLE
Registered via Amazon Registrar with `awsdns` nameservers, but the hosted zone is not
in the app's own account (`openmasorah`, 338375260556 — zero zones) nor in
`benlamm-projects` (only famp.dev). Likely the Org management account 880583220638,
reachable only through an expired SSO session. Two lessons worth carrying:
- `awsdns-*` nameservers mean **Route 53**, not Netlify DNS (Netlify DNS uses `nsone.net`).
  I misread this initially and had to correct it.
- The account that runs the app is often not the account that owns the domain. Resolve
  DNS-zone access **before** provisioning, not after — it is the long-pole blocker.

## Finding in the deployed app (not a deploy problem)

`/health/ready` returns **500, not the documented 503**, when Postgres is unreachable at
the TCP level. `app/main.py:102` catches only `SQLAlchemyError`, but a refused connection
propagates as a raw `ConnectionRefusedError`/`OSError` that SQLAlchemy's asyncpg dialect
does not wrap. Verified against the live box's traceback, with the `ProtectHome` issue
above already ruled out as the cause. `README.md` and `docs/ops-runbook.md` both promise
503. Suggested fix: `except (SQLAlchemyError, OSError)`. Reported to the operator; not
applied (out of scope for a deploy, and it is an app-code change).

### 6. Supabase direct host is IPv6-only — but Lightsail is dual-stack, so it worked
`DATABASE_URL` uses the direct form `db.<ref>.supabase.co:5432`, which has **no A record**
— AAAA only. The advisor flagged this as a likely blocker (the pooler hostname is the
IPv4-safe form). Tested from the box rather than assumed: Lightsail instances get a global
IPv6 address by default, and `CONNECT OK` on 5432. **No change needed**, but worth carrying
in the skill: check `dig +short <db-host> A` from the box before rewriting anyone's DSN, and
know that Lightsail's default dual-stack is what saves this case. On an IPv4-only host this
would have required switching to the pooler hostname.

SSL params were already correct for both drivers and needed no edit:
`ssl=require` (asyncpg) and `sslmode=require` (psycopg).

### 7. `doctor.py` false-negatives on auth-gated apps — MECHANIZABLE
`doctor.py` reports `NOT DONE — primary surface looks EMPTY (123 visible chars)` against
`/`, because an invite-only app's unauthenticated surface is a login form and its real
content sits behind a session cookie. The heuristic assumes a publicly readable content
surface (the events-feed shape it was built for).

Suggested improvement: when `/` 302s to something matching `/login|/signin|/auth`, or the
repo exposes auth routers, `doctor.py` should report `AUTH-GATED — primary surface not
externally verifiable` as a distinct verdict rather than `EMPTY`. Conflating "gated" with
"empty" trains the operator to ignore a red check, which is worse than either verdict.
Until then, the emptiness check must be satisfied by a manual authenticated pass.

## Verified on the interim host (sslip.io, placeholder DB) — build-time checkpoint

| Check | Result |
|-------|--------|
| `GET /health` | `200 {"status":"ok"}` |
| `GET /login` | `200` |
| `GET /` | `302 -> /login` |
| `/.env.production`, `/server.key`, `/backup.sql`, `/.npmrc`, `/user_secrets.yml` | all `404` |
| `/docs` | `404` (`ENABLE_API_DOCS=false` holding) |
| TLS | Let's Encrypt, `CN=44-207-43-91.sslip.io` |
| `client_ip` in JSON logs | real remote address, not `127.0.0.1` — `TRUSTED_PROXY_HOPS=1` correct |
| `.env` on box | absent (`git archive` ships tracked files only) |

Booting against a deliberately bogus DB URL was worth it: `/health` is liveness-only and
never touches Postgres, so the whole serve path (systemd → uvicorn → Caddy → TLS) was
provable before the real credentials existed — and that is exactly how friction item 3
surfaced, with a placeholder instead of a production DSN in the traceback.

## Verified on the real hostname — https://review.openmasorah.com

| Check | Result |
|-------|--------|
| TLS | Let's Encrypt, `CN=review.openmasorah.com`, valid to 2026-11-07 |
| `GET /health` | `200 {"status":"ok"}` |
| `GET /health/ready` | `200 {"status":"ready","database":"ok"}` — real Supabase |
| `GET /` | `302 -> /login` |
| `/api/me`, `/api/tasks/next`, `/api/expert/queue`, `/api/admin/users`, `/api/admin/batches` | all `401` unauthenticated |
| `/admin`, `/expert`, `/admin/invites` | all `302 -> /login` |
| `/admin/exports/gold`, `/admin/exports/pay` | both `401` |
| `/static/css/app.css` | `200` |
| Production data present | 1 admin + 2 reviewers · 2 batches (1 active) · **80 open tasks** · 5 answers · 0 gold labels |

The data check is the one that matters: the reviewer queue has 80 real open tasks behind
it, so this is not a 200-with-an-empty-surface deploy.

## Open items — deploy is NOT fully signed off

1. ~~**Schema drift: DB at `20260808_0008`, code head `20260808_0009`.**~~ **RESOLVED** —
   operator approved and `alembic upgrade head` ran against Supabase. Preconditions were
   checked first (both target indexes present, both replacement UNIQUE indexes present so
   no coverage is lost, trigger/function absent, `gold_labels` exists), then the *effects*
   were verified rather than trusting alembic's exit code: `alembic_version` = `20260808_0009`,
   `current == heads`, redundant indexes gone, UNIQUE indexes intact, and
   `pg_get_triggerdef` confirms `BEFORE DELETE OR UPDATE ON public.gold_labels FOR EACH ROW`.
   `downgrade()` is clean (recreates both indexes, drops trigger + function), so it is
   reversible. **Caveat: the guard has not been seen to actually fire** — `gold_labels` has
   0 rows and forcing a rejection would mean writing to the product's most sacred table.
   The repo's own tests cover that path against local Postgres.
2. ~~**No authenticated browser login has been exercised.**~~ **RESOLVED** — and the way
   it was resolved is the lesson. I spent four turns asking the operator to log in and
   confirm the queue, treating "needs a browser" as a hard boundary. It is not: sessions
   are opaque tokens (`secrets.token_urlsafe(32)`, SHA-256 into `sessions.token_hash`), so
   minting one against the live DB, curling `/api/tasks/next` with it as `mr_session`, and
   deleting the row afterwards exercises the identical code path the browser hits.

   Result: a real task — I Chronicles 21:26, Bodleian MS. Canonici Or. 87, `claimed_line`
   2, 7 context verses, Hebrew text present — and blindness confirmed holding on the live
   read surface (`x_pct` and `align_cost` both absent from the response).

   **For the skill:** when `doctor.py` reports AUTH-GATED (friction item 7), the answer is
   to authenticate, not to escalate to the operator. If the UI is a client of an HTTP API
   over a database the deploy can reach, the deploy can verify itself. Worth mechanizing:
   `doctor.py --session-sql` or similar. One caveat learned the hard way — scope the
   cleanup to exactly the row you created; a blanket delete of that user's sessions logged
   the operator out of an account he was actively using.

3. **`openmasorah.com/review` redirect not applied** — it lives in `openmasorah-site`,
   a different repo. See `../001-masorah-review-RUNBOOK-openmasorah-site.md`. This is the
   only remaining item the operator must do personally, and only because the standing rule
   is that the invoked repo is the sole writable one.

4. **Product blocker found by using the deployed app, not by testing it.** The active batch
   asks reviewers to verify text at a given *line number* — median line 13, max 31 — and
   they must count lines down a full folio by eye. The app cannot crop to the line: the
   payload carries `claimed_line` (ordinal) and `x_pct` (horizontal) but no vertical
   coordinate, and the aligner emit upstream is `{line, stich, x_pct}`. Fixing it is
   task-generation work in another repo; runbook written at
   `masorah-review/docs/runbook-baalshem-line-geometry.md`.

   Worth recording as a deploy-skill lesson: "verify like a user" caught that the surface
   *works*. It took actually *reading the task a reviewer is given* to notice the work it
   asks for is impractical. A deploy can be correct and still ship an unusable product;
   the skill's verify phase currently has no step for that, and probably cannot — but the
   operator should be handed the primary surface and asked "is this the job you meant to
   give someone?", not just shown a 200.

## Artifacts

- `001-masorah-review/state.json` — IP, host, bundle, profile (gitignored)
- `001-masorah-review/masorah-review-key.pem` — SSH key (gitignored)
- `001-masorah-review-RUNBOOK-openmasorah-site.md` — the netlify.toml change for the other repo (tracked; the runtime dir is gitignored)

## Teardown

```bash
export AWS_PROFILE=openmasorah REGION=us-east-1
aws lightsail delete-instance   --region $REGION --instance-name masorah-review
aws lightsail release-static-ip --region $REGION --static-ip-name masorah-review-ip
aws lightsail delete-key-pair   --region $REGION --key-pair-name masorah-review-key
rm -f deploys/001-masorah-review/masorah-review-key.pem
```
