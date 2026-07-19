#!/usr/bin/env python3
"""deploy-concierge analyzer — point it at a repo, get a sized deploy plan.

Usage:
    python3 analyze.py [--json-only] <github-url | local-path>

Output: the JSON deploy plan on stdout, a human-readable summary on stderr
(suppressed with --json-only). No file is written. The plan is what drives
provisioning (Lightsail VM / S3+CloudFront) via local `aws --profile <name>`
commands run by the operator/agent. Goal: compress the *figuring-out* —
stack, DB, port, JVM, memory, cost — into one command.

Exit-code contract (E1 — the driving agent branches on this):
    0 = clean plan, no warnings.
    3 = plan produced but has warnings — still usable, needs a look.
    2 = usage error (missing argument).
    1 = clone/scan failure (bad URL, network, permissions) — a JSON error
        object {"error": ..., "source": ...} is printed on stdout plus a
        one-line message on stderr; no traceback.

Every analysis step below traces to a real failure from deploy #1 (Parshandata).
Dependency-free (stdlib only) so it runs anywhere.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---- signal tables -------------------------------------------------------

LANG_MARKERS = {
    "node": ["package.json"],
    "python": ["requirements.txt", "pyproject.toml", "Pipfile"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "go": ["go.mod"],
    "ruby": ["Gemfile"],
    "php": ["composer.json"],
    "rust": ["Cargo.toml"],
}

DB_SIGNALS = {
    "pg": "postgres", "postgres": "postgres", "psycopg": "postgres", "psycopg2": "postgres",
    "mysql": "mysql", "mysql2": "mysql", "mariadb": "mysql",
    "mongo": "mongodb", "mongoose": "mongodb",
    "redis": "redis", "ioredis": "redis", "sqlite": "sqlite",
}

# schema.prisma's actual `datasource` provider, not the dep name — see
# scan_repo. postgresql/cockroachdb both speak the postgres wire protocol.
PRISMA_PROVIDER_DB = {
    "postgresql": "postgres", "postgres": "postgres", "mysql": "mysql",
    "sqlite": "sqlite", "mongodb": "mongodb", "cockroachdb": "postgres",
}

FRAMEWORK_SIGNALS = [
    "next", "react", "vue", "vite", "svelte", "angular", "nuxt",
    "express", "fastify", "koa", "nestjs", "@nestjs/core",
    "flask", "fastapi", "django", "gunicorn", "uvicorn",
    "spring", "spring-boot", "quarkus", "rails", "sinatra", "laravel",
]
SERVER_FRAMEWORKS = {
    "express", "fastify", "koa", "nestjs", "@nestjs/core", "next", "nuxt",
    "flask", "fastapi", "django", "gunicorn", "uvicorn",
    "spring", "spring-boot", "quarkus", "rails", "sinatra", "laravel",
    "sveltekit", "astro", "remix",
}
STATIC_FRAMEWORKS = {"react", "vue", "vite", "svelte", "angular"}

# Checked BEFORE the generic FRAMEWORK_SIGNALS loop: "@sveltejs/kit".lower()
# contains "svelte", so the generic substring match folded it into static
# "svelte" and shipped an SSR app to S3 (deploy risk — A1).
_EXPLICIT_FRAMEWORK_SIGNALS = [
    ("@sveltejs/kit", "sveltekit"), ("astro", "astro"), ("@remix-run", "remix"),
]

# Lightsail Linux VM bundles (approx monthly USD, 2-vCPU gen). The analyzer
# *estimates*; provisioning confirms the exact price via the API. (ram_mb, class, usd)
VM_BUNDLES = [
    (512, "nano", 5), (1024, "micro", 7), (2048, "small", 12),
    (4096, "medium", 24), (8192, "large", 44), (16384, "xlarge", 84),
    (32768, "2xlarge", 164),
]

# Two learnings belong to the provisioning layer, NOT static analysis. We surface
# them as caveats rather than pretend to detect them. (deploy #1 learnings 4 & 6.)
PROVISIONING_CAVEATS = [
    "Repo auth: clone server-side with platform-owned GitHub creds — a fresh host "
    "has no GitHub identity (deploy #1: private repo failed `git clone` on the VM).",
    "Public IP: on NAT'd hosts (Lightsail) instance metadata `public-ipv4` is EMPTY — "
    "thread the allocated public IP from the provision step into config/DNS, don't query it.",
    "Branch: the default branch may not build — confirm which branch is deployable "
    "(deploy #1: `main` imported an uncommitted file; the fix was on a feature branch).",
]


# ---- pure detection helpers ----------------------------------------------

def detect_databases(deps, files):
    """Token-boundary match, not substring — 'openpgp' and 'gpg-lite' both
    substring-contain 'pg' and were misread as postgres. schema.prisma's
    actual provider (read in scan_repo) is unioned in by the caller, not
    guessed here."""
    found = set()
    for dep in deps:
        tokens = set(re.split(r"[^A-Za-z0-9]+", dep.lower()))
        for sig, name in DB_SIGNALS.items():
            if sig in tokens:
                found.add(name)
    return sorted(found)


def detect_frameworks(deps):
    found = []
    for dep in deps:
        low = dep.lower()
        matched = False
        for sig, name in _EXPLICIT_FRAMEWORK_SIGNALS:
            if sig in low:
                if name not in found:
                    found.append(name)
                matched = True
                break
        if matched:
            continue
        for sig in FRAMEWORK_SIGNALS:
            if sig in low and sig not in found:
                found.append(sig)
    return found


# ---- LEARNING 1: parse the Dockerfile's actual build/run steps -----------
# File-presence alone is too shallow: a `FROM node` image that apt-installs the
# JDK and runs `mvn` is a Node+JVM polyglot, and its *entrypoint* is node.

_APT_RUNTIME_PKGS = {
    "java": ["openjdk", "default-jdk", "default-jre", "maven", "gradle"],
    "python": ["python3", "python3-pip", "python-is-python3"],
    "node": ["nodejs", "npm"],
    "ruby": ["ruby"], "go": ["golang"], "php": ["php"],
}
_BASE_IMAGE_RUNTIME = {
    "node": "node", "python": "python", "openjdk": "java", "eclipse-temurin": "java",
    "amazoncorretto": "java", "golang": "go", "ruby": "ruby", "php": "php", "rust": "rust",
}

def parse_dockerfile(text):
    runtimes, build_steps = [], []
    base = entrypoint_runtime = port = jvm_heap_mb = None

    # join backslash-continued lines first — a multi-line `RUN apt-get ... && \`
    # otherwise splits the apt package list onto a line we never inspect.
    text = re.sub(r"\\\s*\n\s*", " ", text)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        up = line.upper()

        if up.startswith("FROM "):
            tokens = [t for t in line.split()[1:] if not t.startswith("--")]
            if tokens:
                img = tokens[0].split("/")[-1].split(":")[0].lower()
                base = img
                for key, rt in _BASE_IMAGE_RUNTIME.items():
                    if img.startswith(key) and rt not in runtimes:
                        runtimes.append(rt)
        elif up.startswith("RUN "):
            body = line[4:]
            build_steps.append(body)
            low = body.lower()
            for rt, pkgs in _APT_RUNTIME_PKGS.items():
                if any(p in low for p in pkgs) and rt not in runtimes:
                    runtimes.append(rt)
        elif up.startswith("EXPOSE "):
            m = re.search(r"(\d+)", line)
            if m:
                port = int(m.group(1))
        elif up.startswith(("CMD ", "ENTRYPOINT ")):
            low = line.lower()
            # `CMD ["npm","start"]` (also yarn/pnpm/npx) never mentions "node"
            # literally — the runtime is node regardless of the JS package manager.
            if re.search(r"\b(npm|yarn|pnpm|npx)\b", low):
                entrypoint_runtime = "node"
            else:
                for rt in ("node", "java", "python", "ruby", "go", "php"):
                    tok = "python" if rt == "python" else rt
                    if re.search(rf"\b{tok}3?\b", low):
                        entrypoint_runtime = rt
                        break
        # heap flag can appear in ENV or CMD
        if jvm_heap_mb is None:
            jvm_heap_mb = _parse_heap_mb(line)

    return {"base": base, "runtimes": runtimes, "build_steps": build_steps,
            "entrypoint_runtime": entrypoint_runtime, "port": port,
            "jvm_heap_mb": jvm_heap_mb}


def _parse_heap_mb(text):
    """Extract a JVM max-heap floor in MB from -Xmx flags. A heap flag is a HARD
    RAM floor — the #1 reason deploy #1's '$7 nano' guess was wrong."""
    m = re.search(r"-Xmx\s*(\d+)\s*([gGmM])", text)
    if not m:
        return None
    n = int(m.group(1))
    return n * 1024 if m.group(2).lower() == "g" else n


# ---- LEARNING 2: read deploy-intent docs (the ground truth was in-repo) --

_DEPLOY_DOC_FILES = ["hosting-spec.md", "DEPLOY.md", "deploy.md", "DEPLOYMENT.md",
                     "fly.toml", "render.yaml", "render.yml"]

def parse_deploy_docs(path):
    """Scan known deploy-intent files for RAM/CPU/disk/port/platform hints."""
    for fn in _DEPLOY_DOC_FILES:
        fp = os.path.join(path, fn)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, errors="ignore") as f:
                text = f.read()
        except Exception:
            continue
        hints = {"source_file": fn}
        ram = _find_ram_mb(text)
        if ram:
            hints["ram_mb"] = ram
        m = re.search(r"(\d+)\s*v?cpus?\b", text, re.I)
        if m:
            hints["cpu"] = int(m.group(1))
        m = re.search(r"(\d+)\s*GB\s*SSD|disk[^\d]{0,12}(\d+)\s*GB", text, re.I)
        if m:
            hints["disk_gb"] = int(m.group(1) or m.group(2))
        if hints.get("ram_mb") or hints.get("cpu"):
            return hints
    return {}


def _find_ram_mb(text):
    """Pull a RAM figure from prose/tables/toml. Handles '~6 GB', 'memory = "2gb"',
    '6144 MB'. \\b-bounded context ('Remember' must not match 'mem'); takes the
    MAX of all RAM-context matches, not the first (a doc can mention a smaller
    number — e.g. a min — before the real requirement)."""
    best = None
    for m in re.finditer(r"\b(?:memory|RAM|mem)\b[^\n]{0,40}?[~>=]*\s*(\d+(?:\.\d+)?)\s*(gb|mb|g|m)\b",
                         text, re.I):
        n = float(m.group(1)); unit = m.group(2).lower()
        mb = int(n * 1024) if unit.startswith("g") else int(n)
        if best is None or mb > best:
            best = mb
    if best is not None:
        return best
    # bare 'memory = "2gb"' (toml)
    m = re.search(r'memory\s*=\s*["\']?(\d+)\s*(gb|mb|g|m)', text, re.I)
    if m:
        n = int(m.group(1)); unit = m.group(2).lower()
        return n * 1024 if unit.startswith("g") else n
    return None


# ---- LEARNING 3: size the box to memory, not to a flat default -----------

def estimate_required_ram_mb(facts):
    """Return (mb, basis). Priority: explicit deploy-doc > JVM heap + headroom >
    stack baseline. Returns the strongest signal available. A doc-derived
    figure is self-reported by the repo, not verified — cap it at 16GB (a
    huge in-repo claim is more likely a typo/aspiration than ground truth)
    and label the basis accordingly."""
    docs = facts.get("deploy_docs") or {}
    if docs.get("ram_mb"):
        mb = min(docs["ram_mb"], 16384)
        basis = (f"explicit RAM from {docs.get('source_file', 'deploy doc')} "
                 f"(repo-claimed, unverified)")
        return mb, basis

    heap = facts.get("jvm_heap_mb")
    if heap:
        # JVM heap + Node/OS headroom (deploy #1: 4G heap needed ~6G box)
        return heap + 2048, f"JVM heap {heap}MB + 2GB node/OS headroom"

    runtimes = set(facts.get("runtimes") or [])
    if "java" in runtimes:
        return 2048, "java runtime baseline (no heap flag found — verify)"
    if runtimes & {"node", "python", "ruby", "php", "go"}:
        return 1024, "lightweight runtime baseline"
    return 1024, "default baseline"


def _pick_bundle(required_mb):
    for ram, cls, usd in VM_BUNDLES:
        if ram >= required_mb:
            return ram, cls, usd
    return VM_BUNDLES[-1]


# ---- LEARNING 5: catch unresolved local imports before burning a build ---
# deploy #1: `main` imported ./_RecentSearches.svelte, never committed → hard
# build failure only discovered after provisioning.
#
# HIGH PRECISION over recall (tuned against the real repo, which produced false
# positives): only flag relative imports that NAME a concrete source file (explicit
# extension) and whose exact target is absent. We deliberately SKIP:
#   - extensionless specifiers (`./foo`) — resolve via index files / tsconfig paths
#     / codegen; statically unknowable and the #1 false-positive source.
#   - framework virtual modules (`./$types`, `$app/*` etc.) — generated at build.
# The build itself is the backstop for everything we skip. Better to miss a rare
# real one than cry wolf on a repo that builds fine.

_IMPORT_RE = re.compile(r"""['"](\.{1,2}/[^'"]+)['"]""")
_SRC_EXTS = (".svelte", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue")
# only specifiers ending in one of these get checked (explicit-file imports)
_FLAGGABLE_EXTS = (".svelte", ".vue", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

def check_unresolved_imports(path, max_files=600):
    """Returns (missing, truncated). `truncated` is True when max_files was hit
    mid-scan — the caller (scan_repo/recommend_target) must not present a
    truncated result as an exhaustive clean bill of health.

    CAVEAT: macOS/Windows filesystems are case-insensitive by default, so an
    import whose case doesn't match the on-disk filename (which DOES fail a
    real Linux build) passes `os.path.exists` here and is invisible locally —
    this check is only fully trustworthy run on a case-sensitive FS."""
    missing, seen, scanned = [], set(), 0
    truncated = False
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in
                   ("node_modules", ".git", "build", "dist", ".svelte-kit", "target")]
        for fn in files:
            if not fn.endswith(_SRC_EXTS):
                continue
            if scanned >= max_files:
                truncated = True
                return missing, truncated
            scanned += 1
            fp = os.path.join(root, fn)
            try:
                with open(fp, errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            # line-level heuristic only (no parser): a commented-out import
            # must not be flagged, but a real missing import elsewhere in the
            # same file still must be.
            for line in src.splitlines():
                ln = line.lstrip()
                if ln.startswith(("//", "*", "/*")):
                    continue
                for spec in _IMPORT_RE.findall(line):
                    base = spec.rsplit("/", 1)[-1]
                    if base.startswith("$"):                 # virtual module ($types …)
                        continue
                    if not spec.endswith(_FLAGGABLE_EXTS):    # explicit-extension only
                        continue
                    target = os.path.normpath(os.path.join(root, spec))
                    if os.path.exists(target):
                        continue
                    key = (os.path.relpath(fp, path), spec)
                    if key in seen:
                        continue
                    seen.add(key)
                    missing.append({"file": key[0], "import": spec})
    return missing, truncated


# ---- E1: prefilled AskUserQuestion gates ---------------------------------
# The analyzer can size and detect, but three decisions are the operator's,
# not ours: which AWS account eats the spend, which branch actually builds,
# and whether the box should be open or gated. Feed the driving agent
# ready-to-ask questions instead of making it re-derive the context.

def _build_gates(facts, plan):
    spend_ctx = (f"{plan.get('instance_class') or 'static (S3+CloudFront)'}, "
                 f"~${plan['est_monthly_usd']}/mo")
    import_warnings = [f"{m['import']} in {m['file']}"
                        for m in (facts.get("unresolved_imports") or [])]
    branch_ctx = ("; ".join(import_warnings) if import_warnings
                  else "no unresolved-import warnings found")
    privacy_ctx = ("Let's Encrypt publishes the hostname to Certificate "
                   "Transparency logs within minutes of cert issuance — the "
                   "box is internet-discoverable regardless of whether the "
                   "URL is shared.")
    return [
        {"id": "spend",
         "question": f"Confirm target AWS account/profile before provisioning "
                     f"(est. {spend_ctx}). Which profile?",
         "context": spend_ctx},
        {"id": "branch",
         "question": "Which branch is actually deployable?",
         "context": branch_ctx},
        {"id": "privacy",
         "question": "Should this app be open to the public internet or gated "
                     "(app auth / IP allowlist)?",
         "context": privacy_ctx},
    ]


# ---- recommender ---------------------------------------------------------

def recommend_target(facts):
    fw = facts.get("frameworks", [])
    dbs = facts.get("databases", [])
    runtimes = facts.get("runtimes") or ([facts["language"]] if facts.get("language") else [])
    warnings, next_steps = [], []

    # POSITIVE evidence only — an unknown language or no detected frameworks is
    # an absence of signal, not evidence of "static". Deploy #1 friction: a
    # monorepo top-level scan with no markers used to "confidently" ship s3.
    known_lang = facts.get("language") not in (None, "unknown")
    is_static = (known_lang and bool(fw) and all(f in STATIC_FRAMEWORKS for f in fw)
                 and not dbs and not facts.get("has_server", True))

    if is_static:
        plan = {
            "target": "s3-cloudfront", "hosting_model": "static",
            "why": "Static front-end build, no server process or DB. S3 + CloudFront is cheapest correct.",
            "est_monthly_usd": 3, "instance_class": None, "required_ram_mb": None,
            "managed_db": None, "warnings": warnings, "provisioning_caveats": [PROVISIONING_CAVEATS[0]],
            "next_steps": ["Find build command + output dir (dist/ or build/)",
                           "Create S3 bucket, upload build, front with CloudFront",
                           "Connect domain via Route 53 / ACM (cert in us-east-1)"],
        }
        plan["gates"] = _build_gates(facts, plan)
        return plan

    if not known_lang or not fw:
        warnings.append("No positive static evidence (unknown language / no frameworks "
                        "detected) — defaulting to VM; verify manually. Monorepo? Only "
                        "the repo root is scanned.")

    # Server process → size a VM to memory (learnings 3 & 7: VM beats container
    # for a single warm process — Lightsail container large is ~2x the VM price).
    required_mb, basis = estimate_required_ram_mb(facts)
    ram, cls, usd = _pick_bundle(required_mb)

    managed_db = None
    db_persist = [d for d in dbs if d in {"postgres", "mysql", "mongodb"}]
    if db_persist:
        managed_db = db_persist[0]
        next_steps.append(f"Provision managed {managed_db} (~$15/mo) or in-container for MVP")
    # D2/D3(b): app code often assumes no SSL against a managed DB.
    if "postgres" in dbs or "mysql" in dbs:
        next_steps.append("Managed-DB connection URL needs ?sslmode=require "
                          "(app code often sets no SSL).")
    # D2/D3(a): a frontend build (webpack/vite/next/sveltekit) on a small box
    # OOMs before the app ever runs — the app's own RAM needs are irrelevant.
    build_deps = {"next", "vite", "webpack", "@sveltejs/kit"}
    if any(any(bt in d.lower() for bt in build_deps) for d in (facts.get("deps") or [])) \
       and ram < 4096:
        warnings.append("Frontend build (next/vite/webpack) can OOM a small box — "
                        "add 2GB swap before building (see build-recipes.md).")
    # D2/D3(c)
    if facts.get("next_public_env"):
        warnings.append("NEXT_PUBLIC_* vars are inlined at build time — know the "
                        "public host BEFORE `next build`.")
    # D2/D3(d)
    if facts.get("byoc_platform_sdk"):
        warnings.append("Platform SDK detected (Supabase/Vercel) — check whether it "
                        "can be repointed at your own Postgres/host (BYOC) before "
                        "assuming a plain deploy works.")
    if "redis" in dbs:
        warnings.append("Needs Redis — in-container for MVP; ElastiCache later.")
    if "java" in runtimes and not facts.get("jvm_heap_mb") and not (facts.get("deploy_docs") or {}).get("ram_mb"):
        warnings.append("JVM app with no heap flag or RAM doc found — RAM estimate is a guess; verify.")
    if len(runtimes) > 1:
        warnings.append(f"Polyglot stack {runtimes} — build needs all toolchains "
                        f"(entrypoint: {facts.get('entrypoint_runtime') or 'unknown'}).")
    if not facts.get("has_dockerfile"):
        warnings.append("No Dockerfile — generate one or build natively on the box.")

    for m in (facts.get("unresolved_imports") or [])[:5]:
        warnings.append(f"Unresolved import {m['import']} in {m['file']} — will FAIL the build "
                        f"(check the right branch / a file may be uncommitted).")
    if facts.get("import_scan_truncated"):
        warnings.append("import scan truncated at 600 files — clean result is not exhaustive")

    est = usd + (15 if managed_db else 0)
    plan = {
        "target": "lightsail-vm", "hosting_model": "vm",
        "why": f"Long-running server process needing ~{required_mb}MB RAM ({basis}). "
               f"Lightsail VM '{cls}' ({ram}MB) is the cheapest correct fit — and ~half the "
               f"price of an equivalent container service for a single warm process.",
        "est_monthly_usd": est, "instance_class": cls, "required_ram_mb": required_mb,
        "ram_basis": basis, "managed_db": managed_db, "warnings": warnings,
        "provisioning_caveats": PROVISIONING_CAVEATS,
        "next_steps": next_steps + [
            f"Detected port: {facts.get('port') or 'unknown — find EXPOSE/listen'}",
            f"Provision Lightsail VM ({cls}, ~${usd}/mo), build on box or push image",
            "systemd unit + Caddy auto-TLS reverse proxy", "Connect domain + warmup",
        ],
    }
    plan["gates"] = _build_gates(facts, plan)
    return plan


# ---- repo scanner --------------------------------------------------------

_DEP_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+")

def _parse_requirements_txt(text):
    """Per requirement line only — prose elsewhere in the repo never runs
    through this. Strip comments, skip -r/--flags and raw URLs, stop the
    name at the first version/extras specifier."""
    deps = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://", "git+")):
            continue
        m = _DEP_NAME_RE.match(line)
        if m:
            deps.append(m.group(0))
    return deps


def _quoted_dep_names(array_body):
    names = []
    for s in re.findall(r'["\']([^"\']+)["\']', array_body):
        m = _DEP_NAME_RE.match(s.strip())
        if m:
            names.append(m.group(0))
    return names


def _parse_pyproject_toml(text):
    """HIGH PRECISION over recall: only the `dependencies = [...]` array (PEP
    621, [project] table) and arrays under [project.optional-dependencies] —
    a prose `description` field ('mongo-style store') used to whole-file
    tokenize into a phantom mongodb dependency."""
    deps = []
    m = re.search(r"^\s*dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if m:
        deps += _quoted_dep_names(m.group(1))
    sec = re.search(r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", text, re.S)
    if sec:
        for arr in re.finditer(r"=\s*\[(.*?)\]", sec.group(1), re.S):
            deps += _quoted_dep_names(arr.group(1))
    return deps


def _parse_pipfile(text):
    """Section-aware just enough to avoid whole-file tokenizing: only simple
    `name = "..."` lines directly under [packages]/[dev-packages]; nested
    tables (e.g. [packages.requests]) are skipped rather than guessed at."""
    deps = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if section in ("packages", "dev-packages"):
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*=", line)
            if m:
                deps.append(m.group(1))
    return deps


def _parse_pom_xml(text):
    return re.findall(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", text)


def _parse_gradle(text):
    """Quoted 'group:artifact:version' coordinates only — take the artifact
    segment, not every identifier in the build script."""
    deps = []
    for m in re.finditer(r'''["']([\w.\-]+:[\w.\-]+:[\w.\-\[\]()+,]+)["']''', text):
        parts = m.group(1).split(":")
        if len(parts) >= 2:
            deps.append(parts[1])
    return deps


def _read_deps(path, language):
    deps = []
    try:
        if language == "node":
            with open(os.path.join(path, "package.json")) as f:
                pkg = json.load(f)
            for key in ("dependencies", "devDependencies"):
                deps += list(pkg.get(key, {}).keys())
        elif language == "python":
            for fn, parser in (("requirements.txt", _parse_requirements_txt),
                                ("pyproject.toml", _parse_pyproject_toml),
                                ("Pipfile", _parse_pipfile)):
                fp = os.path.join(path, fn)
                if os.path.exists(fp):
                    with open(fp, errors="ignore") as f:
                        deps += parser(f.read())
        elif language == "java":
            for fn, parser in (("pom.xml", _parse_pom_xml),
                                ("build.gradle", _parse_gradle),
                                ("build.gradle.kts", _parse_gradle)):
                fp = os.path.join(path, fn)
                if os.path.exists(fp):
                    with open(fp, errors="ignore") as f:
                        deps += parser(f.read())
    except Exception:
        pass
    return deps


def scan_repo(path):
    files = set(os.listdir(path))
    prisma_provider_db = None
    prisma_schema_fp = os.path.join(path, "prisma", "schema.prisma")
    if os.path.isdir(os.path.join(path, "prisma")) and \
       "schema.prisma" in os.listdir(os.path.join(path, "prisma")):
        files.add("schema.prisma")
        try:
            with open(prisma_schema_fp, errors="ignore") as f:
                schema_text = f.read()
            m_block = re.search(r"datasource\s+\w+\s*\{([^}]*)\}", schema_text, re.S)
            if m_block:
                m = re.search(r'provider\s*=\s*["\'](\w+)', m_block.group(1))
                if m:
                    prisma_provider_db = PRISMA_PROVIDER_DB.get(m.group(1).lower())
        except Exception:
            pass

    # primary language by marker order
    language = "unknown"
    for lang, markers in LANG_MARKERS.items():
        if any(m in files for m in markers):
            language = lang
            break

    deps = _read_deps(path, language)
    frameworks = detect_frameworks(deps)
    databases = detect_databases(deps, files)
    if prisma_provider_db:
        databases = sorted(set(databases) | {prisma_provider_db})

    # all language markers present → polyglot runtime set
    runtimes = [lang for lang, markers in LANG_MARKERS.items()
                if any(m in files for m in markers)]

    docker = {}
    if "Dockerfile" in files:
        with open(os.path.join(path, "Dockerfile"), errors="ignore") as f:
            docker = parse_dockerfile(f.read())
        for rt in docker.get("runtimes", []):
            if rt not in runtimes:
                runtimes.append(rt)

    # D2/D3(c): NEXT_PUBLIC_* vars are inlined into the client bundle at
    # `next build` time — the public host must be known BEFORE building.
    next_public_env = False
    env_example_fp = os.path.join(path, ".env.example")
    if os.path.exists(env_example_fp):
        try:
            with open(env_example_fp, errors="ignore") as f:
                next_public_env = any(l.strip().startswith("NEXT_PUBLIC_") for l in f)
        except Exception:
            pass

    # D2/D3(d): a platform SDK (Supabase client, vercel.json) means the app
    # may assume platform-managed infra that a plain VM/S3 deploy doesn't have.
    byoc_platform_sdk = any(
        d.lower() == "supabase" or d.lower().startswith("@supabase/") for d in deps
    ) or "vercel.json" in files

    deploy_docs = parse_deploy_docs(path)
    unresolved_imports, import_scan_truncated = check_unresolved_imports(path)
    entrypoint = docker.get("entrypoint_runtime")
    has_server = bool(entrypoint) or any(f in SERVER_FRAMEWORKS for f in frameworks) or \
        (language in {"java", "go", "ruby", "php", "rust"}) or \
        (language in {"node", "python"} and not (frameworks and all(f in STATIC_FRAMEWORKS for f in frameworks)))
    # pure static SPA?
    if frameworks and all(f in STATIC_FRAMEWORKS for f in frameworks) and not databases and not entrypoint:
        has_server = False

    return {
        "language": language,
        "runtimes": runtimes or ([language] if language != "unknown" else []),
        "frameworks": frameworks,
        "databases": databases,
        "has_dockerfile": "Dockerfile" in files,
        "has_server": has_server,
        "entrypoint_runtime": entrypoint,
        "port": docker.get("port") or _detect_listen_port(path),
        "jvm_heap_mb": docker.get("jvm_heap_mb"),
        "deploy_docs": deploy_docs,
        "unresolved_imports": unresolved_imports,
        "import_scan_truncated": import_scan_truncated,
        "env_hint": "see .env.example" if ".env.example" in files else None,
        "deps": deps,
        "next_public_env": next_public_env,
        "byoc_platform_sdk": byoc_platform_sdk,
    }


def _detect_listen_port(path):
    df = os.path.join(path, "Dockerfile")
    if os.path.exists(df):
        with open(df, errors="ignore") as f:
            m = re.search(r"EXPOSE\s+(\d+)", f.read())
            if m:
                return int(m.group(1))
    return None


# ---- cli -----------------------------------------------------------------

def _clone_if_url(target):
    if target.startswith(("http://", "https://", "git@")):
        tmp = tempfile.mkdtemp(prefix="concierge-")
        print(f"Cloning {target} -> {tmp} ...", file=sys.stderr)
        subprocess.run(["git", "clone", "--depth", "1", target, tmp], check=True)
        return tmp
    return target


def main(argv):
    json_only = "--json-only" in argv
    argv = [a for a in argv if a != "--json-only"]
    if len(argv) < 2:
        print("usage: analyze.py [--json-only] <github-url | local-path>", file=sys.stderr)
        return 2
    target = argv[1]

    try:
        path = _clone_if_url(target)
    except (subprocess.CalledProcessError, OSError) as exc:
        # clone/scan failure — JSON error on stdout, one-liner on stderr, no
        # traceback (this is what the driving agent parses on exit code 1).
        print(json.dumps({"error": str(exc), "source": target}, indent=2))
        print(f"error: clone failed for {target}: {exc}", file=sys.stderr)
        return 1

    tmp = path if path != target else None
    try:
        facts = scan_repo(path)
        plan = recommend_target(facts)
        print(json.dumps({"source": target, "facts": facts, "plan": plan}, indent=2))

        if not json_only:
            p, fct = plan, facts
            e = sys.stderr
            print("\n" + "=" * 64, file=e)
            print(f"  STACK    : {fct['language']} | runtimes: {', '.join(fct['runtimes'])}"
                  + (f" (entry: {fct['entrypoint_runtime']})" if fct.get('entrypoint_runtime') else ""), file=e)
            print(f"  DATABASE : {', '.join(fct['databases']) or 'none'}", file=e)
            if fct.get("deploy_docs"):
                print(f"  SPEC     : {fct['deploy_docs']}", file=e)
            print(f"  TARGET   : {p['target']}"
                  + (f"  [{p['instance_class']}, ~{p['required_ram_mb']}MB]" if p.get('instance_class') else "")
                  + f"  (~${p['est_monthly_usd']}/mo)", file=e)
            if p.get("ram_basis"):
                print(f"  SIZED BY : {p['ram_basis']}", file=e)
            if p["warnings"]:
                print("  WARNINGS :", file=e)
                for w in p["warnings"]:
                    print(f"     ! {w}", file=e)
            print("  NEXT     :", file=e)
            for s in p["next_steps"]:
                print(f"     -> {s}", file=e)
            print("  PROVISIONING CAVEATS (platform must handle, not the analyzer):", file=e)
            for c in p.get("provisioning_caveats", []):
                print(f"     * {c}", file=e)
            print("=" * 64, file=e)
        return 3 if plan["warnings"] else 0
    finally:
        # tempdir hygiene: only clean up dirs WE created via clone, never a
        # local path the caller passed in.
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
