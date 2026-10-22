"""CLI: fixtures | run | validate. Exit codes: 0 ok, 1 outputs invalid, 2 invalid request or input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import FIXTURES_DIR
from .intake import IntakeError
from .outputs import validate
from .pipeline import RequestError, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m datarefinery")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fx = sub.add_parser("fixtures", help="regenerate the synthetic fixture batches and ground truth")
    fx.add_argument("--out", type=Path, default=FIXTURES_DIR)
    rn = sub.add_parser("run", help="run the pipeline for a request")
    rn.add_argument("--request", type=Path, required=True)
    rn.add_argument("--out", type=Path, required=True)
    rn.add_argument("--fixtures", type=Path, default=FIXTURES_DIR)
    va = sub.add_parser("validate", help="check a run directory against the output contract")
    va.add_argument("outdir", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "fixtures":
        from .fixtures import generate

        for path in generate(args.out):
            print(path.name)
        return 0
    if args.cmd == "validate":
        problems = validate(args.outdir)
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    try:
        params = json.loads(args.request.read_text())
        summary = run(params, args.out, args.fixtures)
    except (RequestError, IntakeError, json.JSONDecodeError, OSError) as err:
        print(f"invalid request or input: {err}", file=sys.stderr)
        return 2
    problems = validate(args.out)
    print(json.dumps(summary, sort_keys=True, indent=2))
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
