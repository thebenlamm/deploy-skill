# deploy — point an agent at a repo, get back a *working app* in your own cloud

> **Not a green checkmark — a working app.** A generic agent stops at HTTP 200. This one
> runs the data pipeline (seed → ingest → enrich) until the app's actual user-facing surface
> renders real content, in *your* AWS account. That gap — between "the bytes are served" and
> "the app works" — is the whole point.

Point [Claude Code](https://docs.claude.com/en/docs/claude-code) at a GitHub repo and say **"deploy this."** The skill drives a deterministic, opinionated flow:

```
analyze → size → provision a Lightsail VM → build on the box → auto-TLS → run the data pipeline → verify the real surface
```

It runs in *your* AWS account and hands you a live, *populated* HTTPS URL. No platform, no lock-in, no markup.

> ℹ️ **Status: a personal tool, maintained but not under active development.** It encodes a
> *proven* deploy path (two real apps shipped end-to-end), used to accelerate real delivery
> work. It is not a product or a venture. Issues/PRs welcome but not actively staffed. See
> **Known limitations** below — there are real, named gaps and that's intentional honesty.

## Why it exists

Generic "deploy my repo" advice reaches for EC2 + Docker + CloudFront — more expensive, more moving parts, and it trips on the same gotchas every time (NAT-empty metadata IPs, private-repo clone failures, JVM heap OOMs, the default branch that doesn't build, and the big one: declaring "done" on a 200 while the app is empty). This skill bakes in a cheaper, simpler path that shipped two real apps end-to-end — a polyglot SvelteKit + JVM worker, and a Next.js + managed-Postgres feed.

## What's in the box

| File | Purpose |
|------|---------|
| `SKILL.md` | The flow, the 3 judgment gates, the gotchas. Claude reads this. |
| `analyze.py` | Dependency-free repo analyzer: detects stack (incl. polyglot), reads deploy-intent docs, sizes to memory, recommends a Lightsail VM + cost, flags broken imports. |
| `test_analyze.py` | Tests for the analyzer (`python3 test_analyze.py`). |
| `provisioning.md` | Copy-paste Lightsail command sequence + systemd/Caddy templates. |
| `build-recipes.md` | Native build-on-box recipes per stack (static / node / python / java / node+jvm). |
| `deploys/_TEMPLATE.md` | Per-deploy friction log — the feedback loop that improves the skill. |

## Install

Clone into your Claude skills directory:

```bash
git clone https://github.com/thebenlamm/deploy-skill ~/.claude/skills/deploy
```

(or clone anywhere and symlink it to `~/.claude/skills/deploy`.) Restart Claude Code; the `deploy` skill is now discoverable.

## Use

In a Claude Code session:

```
/deploy https://github.com/you/your-repo
```

or just *"deploy this repo to my AWS."* Claude will:

1. Run the analyzer and show you a sized plan + cost.
2. **Stop at three gates** — spend (confirm $/mo + account), branch (which one is deployable), privacy (open or gated). It will not spend money or expose data without asking.
3. Provision, build, and hand you the HTTPS URL.

## Prerequisites

- **Claude Code** with skills enabled.
- An **AWS CLI profile** that can create Lightsail resources in your target account (`aws sts get-caller-identity --profile <name>` to confirm).
- The repo **cloned locally** (so private repos work — the skill ships your local tree to the box).

## Requirements & limitations (honest)

- **Not headless.** It's a Claude-driven skill: it needs a session and your answers at the gates. That's deliberate — the judgment calls are where naive automation breaks.
- **AWS / Lightsail only**, today. The recipes are portable in spirit; other clouds aren't wired up.
- **Stack coverage.** Static, Node, Python, Java/Maven, and Node+JVM polyglot are exercised. Exotic stacks may need a new recipe.

### Known gaps (named on purpose)

Two real deploys surfaced six things the human/agent still handles by hand. All six live as
guidance in `SKILL.md`. Four of them — swap-before-build, `sslmode=require`, `NEXT_PUBLIC_*`
build-time inlining, and the Supabase/Vercel BYOC check — are now surfaced by the analyzer as
warnings too; SKILL.md carries the full guidance:

- **Platform/DB-usage detection is manual.** The load-bearing BYOC question — *"is this a
  Vercel/Supabase app, and can I repoint it at my own Postgres?"* — is answered by a human
  grep (`@supabase` usage, raw `DATABASE_URL` vs vendor SDK), not the analyzer.
- **Sizing is for *runtime*, not *build*.** `analyze.py` can recommend a box that's correct
  to run but OOMs the build (`next build`/webpack/Vite need ≥2GB or swap). Add swap proactively.
- **Managed-DB needs `?sslmode=require`** in the connection URL — app code often sets no SSL.
- **`NEXT_PUBLIC_*` is build-time inlined** → you must know the public host *before* `next build`.
- **Migration drift is silent.** "`migrate` succeeded" ≠ "schema matches code"; an orphan
  `.sql` missing from drizzle `meta/_journal.json` is skipped and breaks runtime inserts.
  Sanity-check that the columns the app writes actually exist.
- **`.env.example` secrets aren't surfaced** by the analyzer — read it yourself for required keys.

> These are written down rather than fixed because this is a maintained tool, not an active
> project. If you fold one into `analyze.py` with a test, log it (see the template's
> "folded into analyzer?" gate) so the capability compounds instead of staying a checklist.

## Contributing

If a deploy surfaces something new (this is a maintained tool — PRs welcome, not actively staffed):

1. Add it to a `deploys/NNN-*.md` friction log (use the template).
2. If it's mechanizable, teach `analyze.py` **with a test** (prose in `SKILL.md` is re-read and
   unenforced; code is deterministic — that's how the loop actually compounds).
3. Bump `CHANGELOG.md` and open a PR.

## License

MIT — see `LICENSE`.
