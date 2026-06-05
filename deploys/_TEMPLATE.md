# Deploy Log — <repo name>

> Copy this file to `deploys/NNN-<repo>.md` for each deploy. **This log IS the
> product spec.** Every "Claude had to figure out X" line is a future feature.

- **Deploy #**: 00N
- **Date**:
- **Source repo**:
- **Who / how found**:
- **Charged**: $___  | **Paid?** yes/no  | **Came back?** yes/no/too-early

## Plan (from analyze.py)
- Stack:
- Database:
- Target + est cost:
- Warnings:

## What actually happened
- Time to live (wall clock):
- Domain:
- Final monthly cost (real):
- **Verified like a user?** (primary surface renders REAL content, not just 200): yes/no — proof:
- Data pipeline run (seed → ingest → enrich) + any keys/approval gates:

## Friction log — the gold
> What did the agent/you have to figure out that analyze.py did NOT predict?
> Each line is a candidate capability for the analyzer.
- [ ]
- [ ]

## Compounding gate — DO NOT mark this deploy done until filled
> Prose in SKILL.md is re-read and unenforced; code in analyze.py is deterministic.
> The loop only compounds if learnings become CODE, not diary. For each friction item above:
- Folded into `analyze.py`? (yes / no / N-A-judgment-call):
- Test added?:
- Commit SHA:
- If NOT folded, why deferred (and is it now a documented Known Gap in the README?):

## Manual steps that should be automated
- [ ]

## Verdict
- Did the deploy produce a *working* app (real content on the primary surface), not just a 200?
- Would this have been a one-click deploy if the analyzer caught everything? yes/no — what was missing:
