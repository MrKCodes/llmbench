"""llmbench command line: `run` a sweep, `report` across machines."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__


def _cmd_run(args: argparse.Namespace) -> int:
    # Imported here, not at module top, so `report` and `dashboard` stay
    # dependency-free. Only `run` needs the config loader (pyyaml) and the
    # runner (which pulls in the backends and `requests`); a CI job that only
    # renders reports can run with the standard library alone.
    try:
        from . import config as config_mod
        from .runner import run_sweep
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"'llmbench run' needs the sweep dependencies ({exc.name}). "
            f"Install them with:  pip install -e '.[run]'   "
            f"(or: pip install -r requirements.txt)"
        )

    cfg = config_mod.load(args.config)

    # machine_label is optional; hardware.detect() falls back to the hostname.
    results = run_sweep(
        cfg, machine_label=args.machine, save_output=args.save_output
    )

    # One folder per machine, named by its stable hardware slug, so re-runs on
    # the same box collect as sessions in the same place. Each run is one
    # session file.
    machine_slug = results["machine"]["slug"]
    machine_dir = os.path.join(args.out_dir, machine_slug)
    os.makedirs(machine_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    out_path = os.path.join(machine_dir, f"session-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    ran = sum(1 for c in results["cells"] if c.get("summary"))
    failed = sum(1 for c in results["cells"] if c.get("error"))
    print(f"\nWrote {out_path}  ({ran} cell(s) measured, {failed} failed)")
    if ran == 0:
        print("No cells produced data. Is a backend running and a model loaded?",
              file=sys.stderr)
        return 1
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from .report import build_report

    md = build_report(args.results)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"Wrote {args.out}")
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import build_dashboard

    html = build_dashboard(args.results)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Wrote {args.out}  (open it in a browser)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llmbench",
        description="Benchmark local LLM inference and compare machines.",
    )
    p.add_argument("--version", action="version",
                   version=f"llmbench {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the sweep on this machine")
    run.add_argument("--config", default="config/bench.yaml",
                     help="path to the sweep YAML (default: config/bench.yaml)")
    run.add_argument("--machine", default=None,
                     help="optional friendly label for this machine in the "
                          "report; the results folder is named automatically "
                          "from detected hardware either way")
    run.add_argument("--out-dir", default="results",
                     help="root results directory (default: results/). A "
                          "per-machine subfolder is created under it")
    run.add_argument("--save-output", action="store_true",
                     help="keep generated text of the first measured run per cell")
    run.set_defaults(func=_cmd_run)

    rep = sub.add_parser("report", help="build a Markdown report from result JSONs")
    rep.add_argument("--results", default="results",
                     help="root results directory, searched recursively "
                          "(default: results/)")
    rep.add_argument("--out", default="report.md",
                     help="output Markdown path (default: report.md)")
    rep.set_defaults(func=_cmd_report)

    dash = sub.add_parser("dashboard",
                          help="build an interactive HTML dashboard")
    dash.add_argument("--results", default="results",
                      help="root results directory, searched recursively "
                           "(default: results/)")
    dash.add_argument("--out", default="dashboard.html",
                      help="output HTML path (default: dashboard.html)")
    dash.set_defaults(func=_cmd_dashboard)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
