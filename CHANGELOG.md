# Changelog

This is a **beta** skill that improves with every deploy. Each entry should trace
to a real deploy's friction log.

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
