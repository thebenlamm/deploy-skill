"""Tests for the deploy-concierge analyzer.

Run: python3 test_analyze.py   (or: python3 -m pytest test_analyze.py -q)

Detection + recommendation logic is the part worth testing — it decides what
gets provisioned and how big. Every test below traces to a real failure from
deploy #1 (Parshandata). See deploys/001-parshandata.md.
"""
import json
import os
import tempfile
import analyze


# ====================================================================
# ORIGINAL behavior — must still hold
# ====================================================================

def test_static_spa_goes_to_s3_cloudfront():
    facts = {"language": "node", "frameworks": ["vite", "react"], "runtimes": ["node"],
             "databases": [], "has_dockerfile": False, "has_server": False}
    rec = analyze.recommend_target(facts)
    assert rec["target"] == "s3-cloudfront"
    assert rec["est_monthly_usd"] <= 5


def test_detect_databases_from_deps():
    dbs = analyze.detect_databases(["express", "pg", "prisma", "ioredis"], set())
    assert "postgres" in dbs and "redis" in dbs


def test_detect_frameworks_from_deps():
    assert "next" in analyze.detect_frameworks(["next", "react", "@prisma/client"])


# ====================================================================
# LEARNING 1 — polyglot Dockerfile parsing
# (analyzer called Parshandata "java", missed Node entrypoint)
# ====================================================================

PARSHANDATA_DOCKERFILE = """FROM node:20-bookworm
RUN apt-get update && apt-get install -y openjdk-17-jdk maven wget
WORKDIR /app
COPY . .
RUN mvn -q -DskipTests package
RUN cd frontend-new && npm ci && npm run build
ENV PARSHANDATA_JAVA_OPTS="-XX:+UseG1GC -Xmx4g"
EXPOSE 3000
CMD ["node", "build"]
"""

def test_dockerfile_parse_detects_polyglot():
    d = analyze.parse_dockerfile(PARSHANDATA_DOCKERFILE)
    assert "node" in d["runtimes"]
    assert "java" in d["runtimes"]        # from `apt-get install openjdk + maven`
    assert d["entrypoint_runtime"] == "node"   # CMD ["node", ...]
    assert d["port"] == 3000

def test_dockerfile_parse_extracts_heap_flag():
    d = analyze.parse_dockerfile(PARSHANDATA_DOCKERFILE)
    assert d["jvm_heap_mb"] == 4096       # -Xmx4g


# ====================================================================
# LEARNING 2 — deploy-intent doc parsing (hosting-spec.md existed)
# ====================================================================

HOSTING_SPEC = """# Hosting requirements
| | Target (3 concurrent users) |
|---|---|
| CPU | 2 vCPU |
| RAM | ~6 GB (1 x 4 GB JVM + Node + headroom) |
| Disk | 10 GB SSD |
"""

DOCKERFILE_CONTINUATION = """FROM node:20
RUN apt-get update && \\
    apt-get install -y openjdk-17-jdk maven
CMD ["node", "server.js"]
"""

def test_dockerfile_line_continuation_detects_java():
    d = analyze.parse_dockerfile(DOCKERFILE_CONTINUATION)
    assert "java" in d["runtimes"]

def test_dockerfile_from_platform_flag_is_skipped():
    d = analyze.parse_dockerfile("FROM --platform=linux/amd64 node:20\n")
    assert d["base"].startswith("node")
    assert "node" in d["runtimes"]


def test_deploy_doc_parse_reads_ram():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "hosting-spec.md"), "w") as f:
        f.write(HOSTING_SPEC)
    hints = analyze.parse_deploy_docs(d)
    assert hints["ram_mb"] == 6144        # ~6 GB -> 6144
    assert hints["source_file"] == "hosting-spec.md"

def test_ram_regex_word_boundary_no_false_positive():
    """'Remember' contains the substring 'mem' but not the whole word — must
    not be mistaken for a RAM context."""
    assert analyze._find_ram_mb("Remember to attach a 20 GB volume") is None


def test_ram_regex_takes_max_of_multiple_ram_contexts():
    assert analyze._find_ram_mb("min memory 512mb, recommended memory 2gb") == 2048


def test_deploy_doc_parse_flytoml():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "fly.toml"), "w") as f:
        f.write('[[vm]]\n  memory = "2gb"\n  cpus = 2\n')
    hints = analyze.parse_deploy_docs(d)
    assert hints["ram_mb"] == 2048


# ====================================================================
# LEARNING 3 — memory sizing (heap flag = hard RAM floor)
# ====================================================================

def test_required_ram_prefers_explicit_doc():
    facts = {"deploy_docs": {"ram_mb": 6144, "source_file": "hosting-spec.md"},
             "jvm_heap_mb": 4096}
    mb, basis = analyze.estimate_required_ram_mb(facts)
    assert mb == 6144
    assert "hosting-spec.md" in basis

def test_required_ram_from_heap_flag_when_no_doc():
    facts = {"deploy_docs": {}, "jvm_heap_mb": 4096}
    mb, basis = analyze.estimate_required_ram_mb(facts)
    assert mb >= 4096 + 1024              # heap + node/os headroom
    assert "heap" in basis.lower()


# ====================================================================
# LEARNING 3+7 — sizing recommends the right VM, not a $7 nano,
# and prefers VM over container for a single warm process
# ====================================================================

def test_parshandata_shaped_facts_pick_8gb_vm():
    facts = {"language": "node", "frameworks": ["express"], "runtimes": ["node", "java"],
             "databases": [], "has_dockerfile": True, "has_server": True, "port": 3000,
             "jvm_heap_mb": 4096,
             "deploy_docs": {"ram_mb": 6144, "source_file": "hosting-spec.md"}}
    rec = analyze.recommend_target(facts)
    assert rec["hosting_model"] == "vm"            # not container, not nano
    assert rec["instance_class"] == "large"        # 8 GB tier
    assert 40 <= rec["est_monthly_usd"] <= 50      # ~$44, not $7
    assert rec["required_ram_mb"] == 6144

def test_small_node_app_stays_cheap():
    facts = {"language": "node", "frameworks": ["express"], "runtimes": ["node"],
             "databases": [], "has_dockerfile": True, "has_server": True,
             "deploy_docs": {}, "jvm_heap_mb": None}
    rec = analyze.recommend_target(facts)
    assert rec["instance_class"] in ("micro", "small")
    assert rec["est_monthly_usd"] <= 15


# ====================================================================
# LEARNING 5 — broken-import pre-check (the missing _RecentSearches.svelte)
# ====================================================================

def test_unresolved_import_is_flagged():
    d = tempfile.mkdtemp()
    sub = os.path.join(d, "src", "routes")
    os.makedirs(sub)
    with open(os.path.join(sub, "Panel.svelte"), "w") as f:
        f.write("import RecentSearches from './_RecentSearches.svelte'\n"
                "import ok from './Present.svelte'\n"
                "import lib from '$lib/server/thing'\n"      # alias — must NOT flag
                "import express from 'express'\n")           # npm pkg — must NOT flag
    with open(os.path.join(sub, "Present.svelte"), "w") as f:
        f.write("<div/>")
    missing = analyze.check_unresolved_imports(d)
    names = [m["import"] for m in missing]
    assert "./_RecentSearches.svelte" in names
    assert "./Present.svelte" not in names        # exists
    assert all("$lib" not in n and "express" not in n for n in names)


def test_import_check_ignores_virtual_and_extensionless():
    """High-precision guard: don't cry wolf on SvelteKit virtual modules or
    extensionless (codegen/index-resolved) imports — they false-flagged the real
    Parshandata repo on a branch that built fine."""
    d = tempfile.mkdtemp()
    sub = os.path.join(d, "src")
    os.makedirs(sub)
    with open(os.path.join(sub, "a.ts"), "w") as f:
        f.write("import type { X } from './$types'\n"          # virtual — skip
                "import { y } from './DaiquiriGenerated'\n"      # extensionless — skip
                "import { z } from './also-missing'\n")          # extensionless — skip
    assert analyze.check_unresolved_imports(d) == []


# ====================================================================
# LEARNING 4+6 — surfaced as provisioning caveats, not fake-detected
# ====================================================================

def test_plan_surfaces_provisioning_caveats():
    facts = {"language": "node", "frameworks": ["express"], "runtimes": ["node"],
             "databases": [], "has_dockerfile": True, "has_server": True, "deploy_docs": {}}
    rec = analyze.recommend_target(facts)
    blob = " ".join(rec["provisioning_caveats"]).lower()
    assert "repo" in blob and "auth" in blob       # server-side repo auth
    assert "public ip" in blob or "nat" in blob    # NAT IP threading


# ====================================================================
# Integration — full scan of a Parshandata-shaped repo
# ====================================================================

def test_sveltekit_ssr_is_not_static():
    """SvelteKit is an SSR server framework, not a static-site generator like
    plain svelte — the substring match used to fold '@sveltejs/kit' into
    static 'svelte' and wrongly ship it to S3."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "package.json"), "w") as f:
        json.dump({"dependencies": {"@sveltejs/kit": "^2", "svelte": "^4", "vite": "^5"}}, f)
    facts = analyze.scan_repo(d)
    rec = analyze.recommend_target(facts)
    assert rec["hosting_model"] == "vm"


def test_monorepo_shaped_repo_with_only_readme_defaults_to_vm_with_warning():
    """No language marker, no frameworks (e.g. a monorepo where only the
    top-level README got scanned) is NOT positive static evidence — default
    to VM and warn, never s3-cloudfront."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# monorepo\nSee packages/ for the actual services.\n")
    facts = analyze.scan_repo(d)
    rec = analyze.recommend_target(facts)
    assert rec["target"] == "lightsail-vm"
    blob = " ".join(rec["warnings"]).lower()
    assert "monorepo" in blob and "verify" in blob


def test_rust_language_no_frameworks_goes_to_vm():
    facts = {"language": "rust", "frameworks": [], "runtimes": ["rust"],
             "databases": [], "has_dockerfile": False}
    rec = analyze.recommend_target(facts)
    assert rec["hosting_model"] == "vm"


def test_scan_repo_polyglot_node_java():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "package.json"), "w") as f:
        json.dump({"dependencies": {"express": "^4"}}, f)
    with open(os.path.join(d, "pom.xml"), "w") as f:
        f.write("<project><modelVersion>4.0.0</modelVersion></project>")
    with open(os.path.join(d, "Dockerfile"), "w") as f:
        f.write(PARSHANDATA_DOCKERFILE)
    with open(os.path.join(d, "hosting-spec.md"), "w") as f:
        f.write(HOSTING_SPEC)
    facts = analyze.scan_repo(d)
    assert set(["node", "java"]).issubset(set(facts["runtimes"]))
    assert facts["jvm_heap_mb"] == 4096
    assert facts["deploy_docs"]["ram_mb"] == 6144
    rec = analyze.recommend_target(facts)
    assert rec["instance_class"] == "large"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
