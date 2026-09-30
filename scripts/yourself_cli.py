"""Thin report CLI; optional fixed version observations, no project-content reads."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yourself


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only OS/runtime observations")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--directory")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--include-host", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--versions", action="store_true")
    parser.add_argument("--ports", action="store_true")
    parser.add_argument("--command", action="append")
    args = parser.parse_args(argv)
    try:
        if args.sample_size < 0:
            raise ValueError("sample-size must be non-negative")
        if args.diagnose or args.versions or args.ports or args.command:
            facts = yourself.diagnose(directory=args.directory, include_host=args.include_host,
                                      versions=args.versions, ports=args.ports,
                                      commands=args.command)
        else:
            facts = yourself.collect(directory=args.directory, sample_size=args.sample_size,
                                     include_host=args.include_host)
    except (OSError, ValueError) as error:
        print(type(error).__name__, file=sys.stderr)
        return 2
    render = yourself.to_json if args.format == "json" else yourself.to_markdown
    print(render(facts), end="")
    partial = (facts["directory"] and facts["directory"]["errors"] or
               facts.get("workspace") and facts["workspace"]["errors"] or
               facts.get("listeners") and facts["listeners"]["errors"] or
               any(t["status"] in ("timeout", "execution_error", "nonzero_exit",
                                   "unrecognized_version") for t in facts.get("commands", [])))
    return 2 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
