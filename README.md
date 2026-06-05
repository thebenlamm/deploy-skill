# deploy — a Claude skill for shipping any repo to your own AWS, with minimal friction

> ⚠️ **Beta.** This skill encodes a *proven* deploy path, not a finished product.
> It gets smarter every run: each deploy fills a friction log, and mechanizable
> learnings get folded back into the analyzer. Expect rough edges on stacks it
> hasn't met yet — and PRs that teach it new ones.

Point [Claude Code](https://docs.claude.com/en/docs/claude-code) at a GitHub repo and say **"deploy this."** The skill drives a deterministic, opinionated flow:

```
analyze → size → provision a Lightsail VM → build on the box → auto-TLS → public URL
```

It runs in *your* AWS account and hands you a live HTTPS URL. No platform, no lock-in, no markup.

## Why it exists

Generic "deploy my repo" advice reaches for EC2 + Docker + CloudFront — more expensive, more moving parts, and it trips on the same gotchas every time (NAT-empty metadata IPs, private-repo clone failures, JVM heap OOMs, the default branch that doesn't build). This skill bakes in a cheaper, simpler path that actually shipped a real polyglot app (SvelteKit + a long-lived JVM worker) end-to-end.

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
git clone https://github.com/<you>/deploy-skill ~/.claude/skills/deploy
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
- **Beta coverage.** Static, Node, Python, Java/Maven, and Node+JVM polyglot are exercised. Exotic stacks may need a new recipe — that's what the friction log is for.

## Contributing

The improvement loop is the point. If a deploy surfaced something the skill didn't know:

1. Add it to a `deploys/NNN-*.md` friction log (use the template).
2. If it's mechanizable, teach `analyze.py` (with a test) or add a `build-recipes.md` recipe.
3. Bump `CHANGELOG.md` and open a PR.

## License

MIT — see `LICENSE`.
