# Changelog

A maintained personal tool, not a venture. Entries trace to a real deploy's friction log.

## [0.5.0] — 2026-08-09 — deploy #001 (masorah-review): FastAPI + external Postgres
First Python/FastAPI deploy and first "serve it under an existing marketing site" ask.
All entries trace to `deploys/001-masorah-review.md`:
- **`ProtectHome=tmpfs`, not `true`, in the unit template** — the highest-value fix here.
  `ProtectHome=true` makes Postgres clients' stat of `~/.postgresql/postgresql.key` raise
  `PermissionError` (EACCES) rather than ENOENT, on *every* SSL connect. EACCES is an
  `OSError`, which app-level `except SQLAlchemyError` handlers miss, so the app 500s on a
  DB call that should have worked. Caught before the real credentials went in.
- **`curl -4 -s ifconfig.me`** in §2 — on a dual-stack connection the unflagged form
  returns IPv6 and `put-instance-public-ports` rejects it as an invalid IPv4 CIDR.
- **DNS zone access is the long-pole blocker** — new §"Real domain" guidance: resolve it
  *before* provisioning, sweep every profile for the zone, and read NS records to identify
  the provider (`awsdns-*` = Route 53; `nsone.net` = Netlify DNS; a Netlify-hosted site
  very often has Route 53 DNS). Plus create-record → wait-for-resolution → *then* point
  Caddy, since Let's Encrypt validates against the live A record.
- **New §"Serving an app under an existing marketing site"** — grep for root-absolute
  links before promising `example.com/thing`; prefer a subdomain + **302** over a `200`
  proxy rewrite (cookie scope), and remember a splat rule misses the bare path.
- **Known gap, not yet fixed:** `doctor.py` reports auth-gated apps as `EMPTY`. It needs a
  distinct `AUTH-GATED` verdict — conflating the two trains operators to ignore a red check.

## [0.4.0] — 2026-07-18 — FIX_PLAN remainder: day-2 ops + agent contract + verify/audit tooling
Completes every open row (C4–C7, E1–E4):
- **`doctor.py`** (new): mechanizes "verify like a user" — exit 4 when the primary surface
  is empty; finds pipeline scripts (seed→ingest→enrich), orphan drizzle migrations
  (the silent-drift failure), and required env keys. Wired into SKILL.md's verify phase.
- **`audit.py`** (new): cost sweep over `deploys/*/state.json`; `--live` reconciles against
  AWS and emits teardown commands — only for boxes tagged `managed-by=deploy-concierge`
  (never proposes deleting instances the skill didn't create). 17 tests (`test_doctor.py`).
- **Analyzer agent contract (E1/E2)**: exit codes 0/3/1/2, `--json-only`, a prefilled
  `gates` array (spend/branch/privacy AskUserQuestion feed), clean clone-failure JSON,
  tempdir cleanup; npm/yarn/pnpm CMD → node entrypoint, commented-out imports not flagged,
  truncated import scans say so. 39 analyzer tests.
- **Runbooks (C4–C7)**: redeploy section (stop → re-tar → rebuild → migrate → start →
  re-verify; no second instance), swap block for <4GB boxes, journalctl debugging paths,
  bounded provisioning poll + key-pair write guard, `Restart=always` + unit Description,
  JDK version detected from pom.xml, AZ-fallback and sslip.io-no-SLA notes.

## [0.3.0] — 2026-07-18 — four-lens review executed (Phases 1–3 + C1–C3 of FIX_PLAN.md)
Full-repo review (correctness / security / infra-ops / skill-design); findings cataloged
in `FIX_PLAN.md` and the wrong-answer-producing + structurally-dishonest set fixed:
- **Analyzer P0s** (each with tests; 15 → 32 passing): "confidently static" killed —
  s3-cloudfront now requires positive evidence (SvelteKit SSR / monorepo / Rust repos
  went to S3 before); RAM regex word-bounded + max-match ("Remember … 20 GB" sized a
  $164/mo box); Dockerfile line-continuations joined; DB signals token-matched
  (openpgp ≠ postgres) and Prisma's real datasource provider read; dep files parsed
  precisely instead of whole-file tokenized.
- **Four known gaps mechanized** as analyzer warnings (swap-before-build, sslmode,
  NEXT_PUBLIC inlining, Supabase/Vercel BYOC) and ported into SKILL.md prose — the
  README's claim that they lived there was false for 4 of 6.
- **Loop un-broken**: `deploys/[0-9]*` gitignore blocked exactly the friction logs the
  README solicits; logs are FILES (committed), runtime artifacts live in gitignored DIRS.
- **Runbooks hardened**: static IP (URL/cert survived stop/start was false before) +
  state.json per deploy; app runs as nologin `app` user in /srv (ubuntu = passwordless
  sudo); secrets via root-0600 EnvironmentFile; tar denylist for .env*/keys; port 22
  restricted to operator IP; `npm run build --if-present` (failures no longer swallowed);
  absolute paths (systemd/Caddy never expand `~`).
- Remaining rows (Phase 4 rest + Phase 5: journalctl/redeploy/swap recipes, exit-code
  contract, doctor.py, audit.py) stay open in `FIX_PLAN.md`.

## [0.2.1] — 2026-06-05 — reviewed & parked
Three independent reviews (essentialist, strategy, hard-truths). Outcome: the engineering
is sound; this is a **tool to accelerate delivery work, not a product to build out.** Decision:
maintain, don't actively develop. Changes this release are honesty + discipline only — no new
features (building more was flagged as avoidance):
- README: lead with "working app, not a green checkmark"; add Status (maintained, not active)
  and a **Known gaps** section naming the 6 analyzer gaps that remain SKILL.md prose, not code
  (platform/DB-usage detection, build-vs-runtime RAM, managed-DB SSL, NEXT_PUBLIC build-time,
  migration drift, .env.example secrets).
- `_TEMPLATE.md`: added a **compounding gate** ("folded into analyze.py? test? SHA?") so future
  learnings become code, not diary — and a "verified like a user?" field.
- Honest note: the analyzer is a deploy-#1 artifact; deploy #2's learnings are documented, not
  yet coded. That's a deliberate stop, not an oversight.

## [0.2.0] — 2026-06-05
**Verify like a user — "deployed" ≠ "working".** Added a mandatory post-deploy phase:
run the data pipeline (seed → ingest → enrich), surface required API keys + curation/
approval gates from the repo, check migration-vs-code drift (orphan migrations), and
confirm the PRIMARY user-facing surface renders real content before reporting done.
A 200 with an empty feed is now an explicit failure, not a success.
Driven by deploy #2 (ShoreScene): a "working" deploy that had an empty feed until the
ingest job + Anthropic enrichment + approval were run.

## [0.1.0] — 2026-06-05
Initial release. Proven end-to-end on a polyglot SvelteKit + JVM-worker app.

**Analyzer (`analyze.py`):**
- Polyglot Dockerfile parsing (base image + apt packages + entrypoint + port + JVM heap)
- Deploy-intent doc parsing (`hosting-spec.md`, `fly.toml`, `render.yaml`)
- Memory-based sizing (explicit doc RAM > JVM heap floor > stack baseline)
- Container-vs-VM cost reasoning; Lightsail VM bundle + price recommendation
- High-precision unresolved-import pre-check (catches uncommitted-file build breaks)

**Flow:** analyze → 3 judgment gates (spend / branch / privacy) → Lightsail VM →
native build-on-box → systemd + Caddy auto-TLS → verify → friction log.

**Known gaps / next:**
- Only AWS Lightsail wired up
- Not headless (needs a Claude session)
- Build recipes cover static / node / python / java / node+jvm — more welcome
