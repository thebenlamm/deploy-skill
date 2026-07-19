"""Tests for doctor.py + audit.py's reconcile().

Run: python3 test_doctor.py

Same self-running style as test_analyze.py: plain asserts, __main__ block
running every test_* function. Network/AWS calls are never exercised here —
only the pure/testable halves (visible_text_len, find_* helpers, reconcile).
"""
import json
import os
import tempfile

import doctor
import audit


# ====================================================================
# emptiness heuristic — SKILL.md: a 200 with an empty feed is NOT done
# ====================================================================

def test_sparse_html_looks_empty():
    html = "<html><body><h1>Loading...</h1></body></html>"
    assert doctor.visible_text_len(html) < 100
    assert doctor.visible_text_len(html) < 200   # doctor.py's looks_empty threshold


def test_real_content_not_flagged_empty():
    html = "<html><body>" + "<p>Real venue listing content here.</p>" * 20 + "</body></html>"
    assert doctor.visible_text_len(html) >= 200


def test_scripts_and_styles_stripped_before_counting():
    html = ("<html><head><style>body{color:red}</style></head><body>"
            "<script>var hidden = 'x'.repeat(5000);</script>"
            "<p>short</p></body></html>")
    assert doctor.visible_text_len(html) < 200


# ====================================================================
# JSON row counting for --api checks
# ====================================================================

def test_json_row_count_array():
    assert doctor.json_row_count(json.dumps([1, 2, 3])) == 3


def test_json_row_count_dict_single_array_value():
    assert doctor.json_row_count(json.dumps({"venues": [1, 2]})) == 2


def test_json_row_count_non_countable_returns_none():
    assert doctor.json_row_count(json.dumps({"status": "ok"})) is None
    assert doctor.json_row_count("not json") is None


# ====================================================================
# pipeline script matching (SKILL.md step 2: seed -> ingest -> enrich)
# ====================================================================

def test_pipeline_scripts_matched_from_package_json():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "package.json"), "w") as f:
        json.dump({"scripts": {"db:seed": "x", "ingest:events": "x",
                                "build": "x", "test": "x", "enrich": "x"}}, f)
    scripts = doctor.find_pipeline_scripts(d)
    assert set(scripts) == {"db:seed", "ingest:events", "enrich"}
    assert "build" not in scripts and "test" not in scripts


def test_pipeline_scripts_no_package_json_returns_empty():
    d = tempfile.mkdtemp()
    assert doctor.find_pipeline_scripts(d) == []


# ====================================================================
# drizzle orphan-migration detection (SKILL.md step 4)
# ====================================================================

def test_drizzle_orphan_migration_flagged():
    d = tempfile.mkdtemp()
    mig_dir = os.path.join(d, "drizzle")
    meta_dir = os.path.join(mig_dir, "meta")
    os.makedirs(meta_dir)
    with open(os.path.join(meta_dir, "_journal.json"), "w") as f:
        json.dump({"entries": [{"tag": "0000_init"}]}, f)
    open(os.path.join(mig_dir, "0000_init.sql"), "w").close()
    open(os.path.join(mig_dir, "0001_orphan.sql"), "w").close()

    orphans = doctor.find_orphan_migrations(d)
    assert any("0001_orphan.sql" in o for o in orphans)
    assert not any("0000_init.sql" in o for o in orphans)


def test_no_journal_means_no_orphans():
    d = tempfile.mkdtemp()
    assert doctor.find_orphan_migrations(d) == []


# ====================================================================
# .env.example required keys
# ====================================================================

def test_required_env_keys_parsed():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, ".env.example"), "w") as f:
        f.write("# comment\nDATABASE_URL=postgres://...\nNEXT_PUBLIC_API_URL=https://x\n\nJOB_SECRET=\n")
    keys = doctor.find_required_env(d)
    assert keys == ["DATABASE_URL", "NEXT_PUBLIC_API_URL", "JOB_SECRET"]


# ====================================================================
# audit.reconcile() — tracked / stale / orphan cost-sweep reconciliation
# ====================================================================

def test_reconcile_tracked_instance():
    records = [{"app": "myapp", "monthly_usd": 24}]
    live = [{"name": "myapp", "state": "running"}]
    r = audit.reconcile(records, live)
    assert len(r["tracked"]) == 1 and r["tracked"][0]["app"] == "myapp"
    assert r["stale"] == [] and r["orphans"] == []


def test_reconcile_stale_record():
    """state.json exists but the instance is gone (already torn down by hand)."""
    records = [{"app": "goneapp", "monthly_usd": 12, "_path": "deploys/001-goneapp/state.json"}]
    live = []
    r = audit.reconcile(records, live)
    assert r["tracked"] == []
    assert len(r["stale"]) == 1 and r["stale"][0]["app"] == "goneapp"
    assert r["orphans"] == []


def test_reconcile_orphan_instance():
    """A running deploy-skill-managed instance with no tracked state.json —
    the forgotten-box case. Only managed-by=deploy-concierge instances are
    orphan candidates: we must never propose teardown of a box we didn't create."""
    records = []
    live = [{"name": "forgotten-box", "state": "running", "profile": "p",
             "region": "us-east-1", "managed": True}]
    r = audit.reconcile(records, live)
    assert r["tracked"] == [] and r["stale"] == []
    assert len(r["orphans"]) == 1 and r["orphans"][0]["name"] == "forgotten-box"


def test_reconcile_unmanaged_instance_is_not_an_orphan():
    """An instance WITHOUT the deploy-concierge tag is someone else's box —
    surfaced as unmanaged info, never given teardown commands."""
    records = []
    live = [{"name": "prod-thing-we-didnt-make", "state": "running", "managed": False}]
    r = audit.reconcile(records, live)
    assert r["orphans"] == []
    assert r["unmanaged"] == ["prod-thing-we-didnt-make"]


def test_reconcile_mixed_tracked_stale_orphan():
    records = [{"app": "a", "monthly_usd": 7}, {"app": "b", "monthly_usd": 12}]
    live = [{"name": "a", "state": "running", "managed": True},
            {"name": "c", "state": "running", "managed": True}]
    r = audit.reconcile(records, live)
    assert [t["app"] for t in r["tracked"]] == ["a"]
    assert [s["app"] for s in r["stale"]] == ["b"]
    assert [o["name"] for o in r["orphans"]] == ["c"]


def test_teardown_commands_reference_all_three_resources():
    cmds = audit.teardown_commands("myapp", "us-east-1", "myprofile")
    blob = " ".join(cmds)
    assert "delete-instance" in blob and "release-static-ip" in blob and "delete-key-pair" in blob
    assert "myapp" in blob and "myprofile" in blob


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
