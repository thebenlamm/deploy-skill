#!/usr/bin/env python3
"""doctor.py — mechanizes SKILL.md's "Verify like a user" step. A 200 with
an empty primary surface is a FAILED deploy, not a success (real miss: a
"full stack working" claim on 8 seeded venues while the actual feed was empty).

Usage:
    python3 doctor.py <state.json | https://url> [--repo <local-clone>] \
        [--api <path> ...] [--json-only]

Checks, each tracing to a SKILL.md "verify like a user" bullet:
1. GET / — status + a crude emptiness signal (visible text after stripping
   tags/scripts/styles). <200 chars of visible text -> "looks EMPTY".
2. --api <path> — status + JSON row count (array, or dict with one array value).
3. --repo — package.json pipeline scripts (seed/ingest/scrape/enrich/migrate,
   run in that order); drizzle meta/_journal.json vs *.sql orphan migrations
   (silently skipped by `migrate` -> runtime inserts fail); .env.example keys.

Output: JSON verdict on stdout, human summary on stderr. Exit 0 working /
4 not-done / 1 error. Dependency-free (stdlib only) so it runs anywhere.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

_PIPELINE_RE = re.compile(r"seed|ingest|scrape|enrich|migrate", re.I)

def resolve_url(arg):
    """A state.json path (reads its "url" field, per provisioning.md §2) or
    a raw http(s) URL string."""
    if arg.startswith("http://") or arg.startswith("https://"):
        return arg
    with open(arg) as f:
        state = json.load(f)
    url = state.get("url")
    if not url:
        raise ValueError(f"{arg} has no 'url' field")
    return url

def fetch(url, timeout=10):
    """Isolated network side effect: GET url (follows redirects, urllib's
    default). Returns (status, body_text, content_length)."""
    req = urllib.request.Request(url, headers={"User-Agent": "deploy-doctor/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body.decode("utf-8", errors="ignore"), len(body)
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, body.decode("utf-8", errors="ignore"), len(body)

def visible_text_len(html):
    """Crude emptiness signal: strip <script>/<style> blocks then all tags,
    collapse whitespace, count what's left. This is the pure/testable half
    of the primary-surface check."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text)

def check_primary_surface(url):
    status, body, clen = fetch(url)
    visible = visible_text_len(body)
    return {"status": status, "content_length": clen,
            "visible_text_chars": visible, "looks_empty": visible < 200}

def json_row_count(body):
    """JSON array -> len; dict with exactly one array-valued key -> len of
    that array; anything else -> None (not a countable collection)."""
    try:
        data = json.loads(body)
    except Exception:
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        arrays = [v for v in data.values() if isinstance(v, list)]
        if len(arrays) == 1:
            return len(arrays[0])
    return None

def check_api(base_url, api_path):
    url = base_url.rstrip("/") + "/" + api_path.lstrip("/")
    status, body, _ = fetch(url)
    return {"path": api_path, "status": status, "row_count": json_row_count(body)}

def find_pipeline_scripts(repo_path):
    """package.json scripts matching seed/ingest/scrape/enrich/migrate — run
    in dependency order seed -> ingest -> enrich (SKILL.md step 2)."""
    fp = os.path.join(repo_path, "package.json")
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, errors="ignore") as f:
            pkg = json.load(f)
    except Exception:
        return []
    return sorted(n for n in pkg.get("scripts", {}) if _PIPELINE_RE.search(n))

def find_orphan_migrations(repo_path):
    """drizzle meta/_journal.json lists applied tags; a sibling *.sql whose
    stem isn't in that list is silently skipped by `migrate` -> runtime
    inserts fail with swallowed errors (SKILL.md step 4)."""
    orphans = []
    for root, dirs, _files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        if os.path.basename(root) != "meta":
            continue
        journal_fp = os.path.join(root, "_journal.json")
        if not os.path.isfile(journal_fp):
            continue
        try:
            with open(journal_fp, errors="ignore") as f:
                journal = json.load(f)
        except Exception:
            continue
        tags = {e.get("tag") for e in journal.get("entries", []) if e.get("tag")}
        migrations_dir = os.path.dirname(root)
        for fn in os.listdir(migrations_dir):
            if fn.endswith(".sql") and fn[:-4] not in tags:
                orphans.append(os.path.relpath(os.path.join(migrations_dir, fn), repo_path))
    return sorted(orphans)

def find_required_env(repo_path):
    """.env.example keys, incl. NEXT_PUBLIC_* (build-time inlined — SKILL.md
    gotcha) — surfaced so the operator can supply them, not guess."""
    fp = os.path.join(repo_path, ".env.example")
    if not os.path.exists(fp):
        return []
    keys = []
    with open(fp, errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key:
                keys.append(key)
    return keys

def main(argv):
    p = argparse.ArgumentParser(description="verify like a user, mechanized")
    p.add_argument("target", help="state.json path or https:// URL")
    p.add_argument("--repo", help="local clone for pipeline/migration/env checks")
    p.add_argument("--api", action="append", default=[], help="content API path (repeatable)")
    p.add_argument("--json-only", action="store_true")
    args = p.parse_args(argv[1:])

    try:
        url = resolve_url(args.target)
        surface = check_primary_surface(url)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    apis = []
    for path in args.api:
        try:
            apis.append(check_api(url, path))
        except Exception as e:
            apis.append({"path": path, "status": None, "row_count": None, "error": str(e)})

    pipeline_scripts, orphan_migrations, required_env = [], [], []
    if args.repo:
        pipeline_scripts = find_pipeline_scripts(args.repo)
        orphan_migrations = find_orphan_migrations(args.repo)
        required_env = find_required_env(args.repo)

    result = {
        "url": url, "http_status": surface["status"],
        "visible_text_chars": surface["visible_text_chars"],
        "looks_empty": surface["looks_empty"], "apis": apis,
        "pipeline_scripts": pipeline_scripts,
        "orphan_migrations": orphan_migrations, "required_env": required_env,
    }
    print(json.dumps(result, indent=2))
    exit_code = 4 if surface["looks_empty"] else 0
    if args.json_only:
        return exit_code

    e = sys.stderr
    print("\n" + "=" * 64, file=e)
    print(f"  URL      : {url}", file=e)
    print(f"  PRIMARY  : {surface['status']}  ({surface['content_length']} bytes, "
          f"{surface['visible_text_chars']} visible text chars)", file=e)
    for a in apis:
        print(f"  API      : {a['path']} -> {a['status']}  rows={a['row_count']}", file=e)
    if pipeline_scripts:
        print(f"  PIPELINE : {', '.join(pipeline_scripts)}  (run seed -> ingest -> enrich)", file=e)
    if orphan_migrations:
        print("  ORPHANS  : silently skipped by migrate -> runtime inserts fail:", file=e)
        for o in orphan_migrations:
            print(f"     ! {o}", file=e)
    if required_env:
        print(f"  ENV KEYS : {', '.join(required_env)}", file=e)
    print("=" * 64, file=e)
    if surface["looks_empty"]:
        print(f"VERDICT: NOT DONE — primary surface looks EMPTY "
              f"({surface['visible_text_chars']} visible chars)", file=e)
    else:
        print("VERDICT: WORKING (surface has content)", file=e)
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
