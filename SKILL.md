---
name: deploy
description: Use when the user wants to deploy, host, or "put online" a GitHub repo / app on their own AWS with minimal friction — "/deploy <url>", "deploy this repo", "host this app", "get this online cheap". Drives a proven flow: analyze → size → provision a Lightsail VM → build on the box → auto-TLS → public URL.
---

# deploy (BETA)

> **Status: beta — improves with every run.** This skill encodes a *proven* deploy
> path, not a finished product. After each deploy, fill a friction log (step 6) and
> fold mechanizable learnings back into `analyze.py`. Expect rough edges on stacks
> it hasn't met yet — when it hits one, capture it; don't paper over it.

Deploy any repo to the user's AWS with the least friction. This skill is
OPINIONATED on purpose: a generic agent picks a more expensive, more complex path
(EC2 + Docker + CloudFront) and misses hard-won gotchas. Follow the defaults below
unless a specific fact forces otherwise.

**Skill dir:** the analyzer + references ship alongside this file. When installed as
a Claude skill that's `~/.claude/skills/deploy/`; call it `<SKILL_DIR>` below.

## Prerequisites (the user provides)
- An **AWS CLI profile** with rights to create Lightsail resources in the target
  account — an Org sub-account, a dedicated account, or any profile. Confirm:
  `aws sts get-caller-identity --profile <PROFILE>`.
- The repo cloned **locally** (the operator's machine has GitHub creds; a fresh VM
  does not — this matters for private repos).

## Opinionated defaults (these beat the "obvious" choices)

- **Lightsail VM**, not EC2 and not a Lightsail *container*. Flat price, built-in
  static IP + firewall, ~half a container's price for a single warm process. Driven
  fine by the local `aws --profile`.
- **Build natively on the box**, not Docker. Operators often lack Docker locally, and
  baked-in data makes images huge. `apt install` toolchains, build, run via systemd.
- **TLS via Caddy + `<dashed-ip>.sslip.io`**, not CloudFront. Real Let's Encrypt cert,
  auto-renew, one line. CloudFront over a dynamic app is a caching footgun.
- **Ship source by tar-piping the local clone** to the box, not `git clone` on the box.

## The flow

1. **Analyze.** `python3 <SKILL_DIR>/analyze.py <github-url|path>`. Detects polyglot
   stacks, reads `hosting-spec.md`/`fly.toml`, sizes to memory (JVM heap = hard RAM
   floor), recommends a Lightsail VM class + cost, flags unresolved imports. Trust its
   size; verify its warnings.
2. **GATES — stop and ask** (each needed a human on real deploys). See below.
3. **Provision.** Key + VM + ports + public IP. See `provisioning.md`.
4. **Build on box.** tar-pipe the clone → per-stack recipe → systemd → Caddy. See `build-recipes.md`.
5. **Verify.** A real request (`curl https://<host>/healthz` or `/`), then any documented warmup.
6. **Log it (beta loop).** Copy `<SKILL_DIR>/deploys/_TEMPLATE.md` → `deploys/NNN-<repo>.md`,
   fill the **friction log** — every "had to figure out X" is a feature. Fold mechanizable
   learnings into `analyze.py` and bump `CHANGELOG.md`.

## Judgment gates — ASK, never assume

| Gate | Why | Ask before |
|------|-----|------------|
| **Spend** | Creating billable resources. | Provisioning. `AskUserQuestion` with the VM size + $/mo from the analyzer; confirm the target account/profile. |
| **Branch** | The default branch may not build (real: `main` imported an uncommitted file; the fix was on a feature branch). | Pushing source. If the analyzer flags unresolved imports OR the build fails, ask which branch is deployable. |
| **Privacy / auth** | The app may expose data publicly or need an admin token / gate. | Sharing the URL. Surface what the repo's own docs flag; let the user decide. |

## Gotchas (carry these — a generic agent misses them)

- **NAT public IP:** on Lightsail, instance metadata `public-ipv4` is **empty**. Carry the
  IP from the provision step (`get-instance`) into Caddy config / DNS — never query on-box.
- **Private repo on a fresh box:** `git clone` fails (no creds). tar-pipe the local clone.
- **Caddy keyring path:** the key must live where the sources list's `signed-by=` points
  (`/usr/share/keyrings/caddy-stable-archive-keyring.gpg`), or apt rejects it.
- **JVM heap vs box RAM:** pin `-Xmx` to fit (≈heap + 2GB box); unpinned `MaxRAMPercentage`
  on a small box OOM-kills.
- **Case-sensitive imports:** an import that works on macOS can fail on Linux (real filename casing).
- **Secrets:** generate tokens locally into the deploy dir's `secrets.env` (gitignored).
  Never commit keys/tokens; never bake them into a build.

## Credentials & where things run

Provisioning runs **locally** via `aws --profile <PROFILE>` + Bash (and SSH to the box).
If you have a remote AWS MCP, note it's usually a single fixed account and can't cross
accounts cleanly — drive Lightsail through the local profile.

## Quick reference

- Analyzer: `analyze.py <url>` → JSON plan + human summary on stderr. Tests: `python3 test_analyze.py`.
- Provisioning command sequence + systemd/Caddy templates: **`provisioning.md`**.
- Per-stack build-on-box recipes (static / node / python / java-maven / node+jvm): **`build-recipes.md`**.
