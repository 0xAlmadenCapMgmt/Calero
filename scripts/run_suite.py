#!/usr/bin/env python3
"""
Sequential run + log harness for the Calero suite.

Runs the components in order — core → adapters → judgment demo → enforcement
testbed → LLM adversarial layer — capturing each stage's full transcript to a
timestamped runs/<ts>/ directory, alongside a summary.md and manifest.json, and
updating runs/latest. Deterministic stages always run; live-LLM stages run only
when ANTHROPIC_API_KEY is set and `anthropic` is importable in the target Python
(otherwise they are SKIPPED, so the harness completes end-to-end either way).

    python scripts/run_suite.py                 # full sequence
    python scripts/run_suite.py --only core     # a single stage
    python scripts/run_suite.py --turns 6       # adversarial conversation length

Run it with a venv that has `anthropic` (+ a key) to exercise the live stages,
e.g. alice-bob/.venv/bin/python scripts/run_suite.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"


def probe_env(py: str) -> tuple[bool, bool]:
    """Returns (have_pkg, have_llm): whether `anthropic` imports in the target
    Python, and whether that plus an API key enables live model calls."""
    import os
    have_pkg = subprocess.run([py, "-c", "import anthropic"], capture_output=True).returncode == 0
    have_llm = have_pkg and bool(os.environ.get("ANTHROPIC_API_KEY"))
    return have_pkg, have_llm


# Step gate levels: None → always; "pkg" → needs the anthropic package (no key);
# "llm" → needs the package AND an API key (a live model call).
def build_stages(py: str, turns: int, run_dir: Path) -> list[dict]:
    """Each stage: name, proves, and ordered steps. A step is (label, argv, gate)."""
    adv_audit = run_dir / "adversarial-audit"
    return [
        {"name": "core", "proves": "Generic engine: platform controls, approval-token lifecycle, NullAdapter.",
         "steps": [("core tests", [py, "-m", "pytest", "tests/test_core.py", "-v"], None)]},
        {"name": "adapters", "proves": "Both adapters + shared derivation; two demos run.",
         "steps": [
             ("adapter tests", [py, "-m", "pytest", "tests/test_coinbase_adapter.py", "tests/test_payments_adapter.py", "-v"], None),
             ("coinbase demo", [py, "-m", "adapters.coinbase.demo"], None),
             ("payments demo", [py, "-m", "adapters.payments.demo"], None),
         ]},
        {"name": "judgment", "proves": "alice-bob: intents judged live by the engine; nothing executes.",
         "steps": [
             ("offline tests", [py, "-m", "pytest", "alice-bob/tests/", "-v"], "pkg"),
             ("live dialogue", [py, "alice-bob/dialogue.py", "--turns", "2"], "llm"),
         ]},
        {"name": "enforcement", "proves": "treasury-desk: governed agents move real funds; invariants hold.",
         "steps": [
             ("scenario", [py, "treasury-desk/scenario.py"], None),
             ("tests", [py, "-m", "pytest", "treasury-desk/tests/test_treasury_desk.py", "-v"], None),
         ]},
        {"name": "adversarial", "proves": "Subverted LLM cannot exfiltrate; invariants are the oracle.",
         "steps": [
             ("adversarial run", [py, "treasury-desk/adversarial.py", "--turns", str(turns),
                                  "--attacker", "david", "--audit-dir", str(adv_audit)], "llm"),
             ("property test", [py, "-m", "pytest", "treasury-desk/tests/test_adversarial.py", "-v"], "llm"),
         ]},
    ]


def run_stage(stage: dict, have_pkg: bool, have_llm: bool, log_path: Path) -> dict:
    lines: list[str] = [f"# stage: {stage['name']}\n# proves: {stage['proves']}\n"]
    steps_meta: list[dict] = []
    any_ran = False
    failed = False
    start = time.time()

    for label, argv, gate in stage["steps"]:
        header = f"\n{'=' * 70}\n$ {' '.join(argv)}"
        gated_off = (gate == "pkg" and not have_pkg) or (gate == "llm" and not have_llm)
        if gated_off:
            why = "anthropic not installed" if gate == "pkg" else "no ANTHROPIC_API_KEY / anthropic"
            lines.append(header + f"\n[SKIPPED — {why}]\n")
            steps_meta.append({"label": label, "skipped": True, "returncode": None})
            continue
        any_ran = True
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        lines.append(header + f"\n{proc.stdout}{proc.stderr}\n[exit {proc.returncode}]\n")
        steps_meta.append({"label": label, "skipped": False, "returncode": proc.returncode})
        if proc.returncode != 0:
            failed = True

    duration = round(time.time() - start, 2)
    status = "FAIL" if failed else ("SKIP" if not any_ran else "PASS")
    log_path.write_text("".join(lines))
    return {"name": stage["name"], "proves": stage["proves"], "status": status,
            "duration_s": duration, "log": log_path.name, "steps": steps_meta}


def write_reports(run_dir: Path, ts: str, py: str, have_pkg: bool, have_llm: bool,
                  results: list[dict]) -> str:
    overall = "FAIL" if any(r["status"] == "FAIL" for r in results) else "PASS"
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}

    md = [f"# Calero suite run — {ts}", "",
          f"- Python: `{py}`",
          f"- anthropic package: **{'present' if have_pkg else 'absent'}**; "
          f"live model (key): **{'available' if have_llm else 'unavailable'}**",
          f"- Overall: **{overall}**", "",
          "| # | Stage | Status | Duration | Log | Proves |",
          "|---|-------|--------|----------|-----|--------|"]
    for i, r in enumerate(results, 1):
        md.append(f"| {i} | {r['name']} | {icon.get(r['status'],'')} {r['status']} | "
                  f"{r['duration_s']}s | [{r['log']}]({r['log']}) | {r['proves']} |")
    (run_dir / "summary.md").write_text("\n".join(md) + "\n")

    (run_dir / "manifest.json").write_text(json.dumps(
        {"timestamp": ts, "python": py, "have_pkg": have_pkg, "have_llm": have_llm,
         "overall": overall, "stages": results}, indent=2) + "\n")
    return overall


def update_latest(run_dir: Path) -> None:
    latest = RUNS / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_dir.name)
    except OSError:
        (RUNS / "LATEST.txt").write_text(run_dir.name + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--turns", type=int, default=4, help="adversarial conversation length")
    parser.add_argument("--python", default=sys.executable, help="interpreter to run stages with")
    parser.add_argument("--only", help="run only this stage (core/adapters/judgment/enforcement/adversarial)")
    args = parser.parse_args()

    py = args.python
    have_pkg, have_llm = probe_env(py)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir = RUNS / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    stages = build_stages(py, args.turns, run_dir)
    if args.only:
        stages = [s for s in stages if s["name"] == args.only]
        if not stages:
            sys.exit(f"unknown stage: {args.only}")

    live = "ENABLED" if have_llm else ("no key" if have_pkg else "no anthropic")
    print(f"Calero suite → {run_dir.relative_to(ROOT)}  (live-LLM stages: {live})\n")
    results = []
    n = len(stages)
    for i, stage in enumerate(stages, 1):
        log_path = run_dir / f"{i:02d}-{stage['name']}.log"
        live_hint = (" (live model — may take a while)"
                     if have_llm and any(g == "llm" for _, _, g in stage["steps"]) else "")
        # Show a running indicator before the (possibly slow) stage starts, then
        # overwrite it in place with the result once the stage finishes.
        print(f"  … {stage['name']:<12} [{i}/{n}] running…{live_hint}", end="", flush=True)
        r = run_stage(stage, have_pkg, have_llm, log_path)
        results.append(r)
        mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(r["status"], "?")
        print(f"\r  {mark} {stage['name']:<12} {r['status']:<5} {r['duration_s']:>6}s  "
              f"→ {r['log']}" + " " * 40)

    overall = write_reports(run_dir, ts, py, have_pkg, have_llm, results)
    update_latest(run_dir)
    bar = "─" * 60
    print(f"\n{bar}\n  DONE — Overall: {overall}"
          f"\n  Logs: {run_dir.relative_to(ROOT)}/  (open summary.md for the table)\n{bar}")
    sys.exit(1 if overall == "FAIL" else 0)


if __name__ == "__main__":
    main()
