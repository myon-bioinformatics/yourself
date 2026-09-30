"""Thin report CLI; no subprocess or project-content inspection."""
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
    args = parser.parse_args(argv)
    try:
        if args.sample_size < 0:
            raise ValueError("sample-size must be non-negative")
        facts = yourself.collect(directory=args.directory, sample_size=args.sample_size,
                                 include_host=args.include_host)
    except (OSError, ValueError) as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 2
    render = yourself.to_json if args.format == "json" else yourself.to_markdown
    print(render(facts), end="")
    return 2 if facts["directory"] and facts["directory"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
