#!/usr/bin/env python3
"""audit.py — cost sweep across deploys/*/state.json. The operator's real
risk isn't a bad deploy, it's a forgotten $24/mo box nobody remembers to
tear down.

Usage:
    python3 audit.py [--deploys-dir deploys] [--profile <p> ...] [--live] [--json-only]

Without --live: reports tracked state.json records only (app, host, $/mo,
age) — no AWS calls. With --live: runs `aws lightsail get-instances` per
distinct profile (state-file profiles union any --profile flags) and
reconciles against the tracked records:
  - RUNNING + tracked            -> normal
  - state.json, no live instance -> stale record (already torn down)
  - live instance, no state.json -> ORPHAN (likely forgotten) -> teardown cmds

Output: JSON on stdout, human summary on stderr. Exit 0, or 5 if orphans
found. Dependency-free (stdlib only). Reconciliation is a pure function
(reconcile) separate from the subprocess wrapper so it's testable without
ever calling aws.
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys


def load_state_records(deploys_dir):
    """Every deploys/*/state.json (provisioning.md §2 shape). Missing dir ->
    empty list, not an error."""
    records = []
    for fp in sorted(glob.glob(os.path.join(deploys_dir, "*", "state.json"))):
        try:
            with open(fp, errors="ignore") as f:
                state = json.load(f)
        except Exception:
            continue
        state["_path"] = fp
        state["age_days"] = _age_days(state.get("created"))
        records.append(state)
    return records


def _age_days(created):
    """'created' is UTC ISO-8601 (provisioning.md's `date -u +%FT%TZ`)."""
    if not created:
        return None
    try:
        dt = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - dt).days
    except Exception:
        return None


def get_live_instances(profile, region):
    """The only function that shells out to aws — isolated so reconcile()
    stays pure/testable. Never called from tests. Carries the tags so
    reconcile can tell OUR boxes (managed-by=deploy-concierge, set at
    create-instances) from boxes this skill never created."""
    cmd = ["aws", "lightsail", "get-instances", "--profile", profile,
           "--region", region, "--query",
           "instances[].{name:name,state:state.name,tags:tags}", "--output", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"aws get-instances failed for {profile}/{region}: {out.stderr.strip()}")
    instances = json.loads(out.stdout)
    for inst in instances:
        inst["managed"] = any(t.get("key") == "managed-by" and t.get("value") == "deploy-concierge"
                              for t in (inst.pop("tags", None) or []))
    return instances


def teardown_commands(app, region, profile):
    """Mirrors provisioning.md's Teardown section — emitted for orphans so
    the operator can act without re-deriving the commands."""
    return [
        f"aws lightsail delete-instance --region {region} --instance-name {app} --profile {profile}",
        f"aws lightsail release-static-ip --region {region} --static-ip-name {app}-ip --profile {profile}",
        f"aws lightsail delete-key-pair --region {region} --key-pair-name {app}-key --profile {profile}",
    ]


def reconcile(state_records, live_instances):
    """Pure function: state_records (parsed state.json dicts, may include
    'profile'/'region'/'app'/'monthly_usd') vs live_instances (flat list of
    {"name", "state", ...} across every profile/region queried). No I/O."""
    live_by_name = {i["name"]: i for i in live_instances}
    tracked_names = {r["app"] for r in state_records if r.get("app")}

    tracked, stale = [], []
    for r in state_records:
        name = r.get("app")
        if name and name in live_by_name:
            tracked.append({"app": name, "state": live_by_name[name].get("state"),
                             "monthly_usd": r.get("monthly_usd")})
        else:
            stale.append({"app": name, "path": r.get("_path")})

    # Orphan = OURS (managed-by=deploy-concierge tag) and untracked — only
    # those get teardown commands. An untracked instance WITHOUT our tag is
    # someone else's box: report it as unmanaged info, never propose deletion.
    untracked = [i for i in live_instances if i.get("name") not in tracked_names]
    orphans = [dict(i) for i in untracked if i.get("managed")]
    unmanaged = sorted(i.get("name") for i in untracked if not i.get("managed"))
    return {"tracked": tracked, "stale": stale, "orphans": orphans, "unmanaged": unmanaged}


def main(argv):
    p = argparse.ArgumentParser(description="cost sweep across deploys")
    p.add_argument("--deploys-dir", default="deploys")
    p.add_argument("--profile", action="append", default=[], help="extra profile to check (repeatable)")
    p.add_argument("--live", action="store_true", help="reconcile against `aws lightsail get-instances`")
    p.add_argument("--json-only", action="store_true")
    args = p.parse_args(argv[1:])

    records = load_state_records(args.deploys_dir)
    total_usd = sum(r.get("monthly_usd") or 0 for r in records)
    result = {
        "deploys_dir": args.deploys_dir,
        "state_records": [{"app": r.get("app"), "host": r.get("host"),
                            "monthly_usd": r.get("monthly_usd"), "age_days": r.get("age_days")}
                           for r in records],
        "total_monthly_usd_tracked": total_usd,
    }

    exit_code = 0
    if args.live:
        profiles = sorted(set(args.profile) | {r["profile"] for r in records if r.get("profile")})
        live, errors = [], []
        for profile in profiles:
            region = next((r["region"] for r in records
                            if r.get("profile") == profile and r.get("region")), "us-east-1")
            try:
                for inst in get_live_instances(profile, region):
                    inst = dict(inst); inst["profile"] = profile; inst["region"] = region
                    live.append(inst)
            except Exception as e:
                errors.append(str(e))
        recon = reconcile(records, live)
        for o in recon["orphans"]:
            o["teardown"] = teardown_commands(
                o.get("name"), o.get("region", "us-east-1"), o.get("profile", "<PROFILE>"))
        result["reconciliation"] = recon
        if errors:
            result["errors"] = errors
        if recon["orphans"]:
            exit_code = 5

    print(json.dumps(result, indent=2))
    if args.json_only:
        return exit_code

    e = sys.stderr
    print("\n" + "=" * 64, file=e)
    print(f"  TRACKED  : {len(records)} deploy(s), ${total_usd}/mo", file=e)
    for r in records:
        print(f"     - {r.get('app')}  ({r.get('host')})  ${r.get('monthly_usd')}/mo  "
              f"age={r.get('age_days')}d", file=e)
    if args.live:
        recon = result.get("reconciliation", {})
        print(f"  LIVE     : {len(recon.get('tracked', []))} tracked, "
              f"{len(recon.get('stale', []))} stale, {len(recon.get('orphans', []))} orphan(s)", file=e)
        for o in recon.get("orphans", []):
            print(f"     ! ORPHAN {o.get('name')} ({o.get('profile')}/{o.get('region')}) "
                  f"— no state.json, likely forgotten. Teardown:", file=e)
            for cmd in o.get("teardown", []):
                print(f"         {cmd}", file=e)
        for s in recon.get("stale", []):
            print(f"     ? STALE record {s['app']} ({s['path']}) — instance gone, state.json remains", file=e)
        if recon.get("unmanaged"):
            print(f"     i {len(recon['unmanaged'])} unmanaged instance(s) (no deploy-concierge "
                  f"tag — not ours, no action): {', '.join(recon['unmanaged'])}", file=e)
        for err in result.get("errors", []):
            print(f"     ERROR: {err}", file=e)
    print("=" * 64, file=e)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
