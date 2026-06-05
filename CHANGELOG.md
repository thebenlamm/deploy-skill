# Changelog

A maintained personal tool, not a venture. Entries trace to a real deploy's friction log.

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
