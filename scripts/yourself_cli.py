"""Thin report CLI; optional fixed version observations, no project-content reads."""
import argparse
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yourself


class UsageError(ValueError):
    """A safe, fixed usage diagnostic (never contains user input)."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message can contain arbitrary argv, including credentials.
        for option in self._option_string_actions:
            if message.startswith("argument " + option + ":"):
                raise UsageError(option + ": invalid or missing value; see --help")
        raise UsageError("Invalid arguments; see --help")


def _json_requested(argv):
    # Honor the last explicit format, including on parse failure. Stop at '--'.
    value = None
    for index, token in enumerate(argv):
        if token == "--":
            break
        if token.partition("=")[0] in ("--f", "--fo", "--for", "--form", "--forma", "--format") and "=" in token:
            value = token.partition("=")[2]
        elif token in ("--f", "--fo", "--for", "--form", "--forma", "--format"):
            value = argv[index + 1] if index + 1 < len(argv) else None
    return value == "json"


def _error(code, message, exit_code, json_mode, facts=None):
    error = {"code": code, "message": message, "exit_code": exit_code}
    if json_mode:
        payload = {"schema_version": 1, "error": error} if facts is None else dict(facts, error=error)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        if facts is not None:
            print(yourself.to_markdown(facts), end="")
        print("error: " + code + ": " + message, file=sys.stderr)
    return exit_code


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = _json_requested(argv)
    parser = Parser(description="Read-only OS/runtime observations",
                    epilog="Exit codes: 0 success; 1 execution failure; 2 invalid input; "
                           "3 partial observations. JSON errors are written to stdout; "
                           "text errors to stderr. Successful report schemas are unchanged.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown",
                        help="Output format (default: markdown); JSON includes structured errors")
    parser.add_argument("--directory", help="Observe a directory shallowly; no file-content reads or symlink roots")
    parser.add_argument("--sample-size", type=int, default=10,
                        help="Maximum directory name sample, non-negative (default: 10; collect mode only)")
    parser.add_argument("--include-host", action="store_true", help="Include the host name (may be sensitive)")
    parser.add_argument("--diagnose", action="store_true", help="Report tool presence and optional workspace diagnostics")
    parser.add_argument("--versions", action="store_true", help="Run fixed version commands; implies diagnostics")
    parser.add_argument("--ports", action="store_true", help="Observe listener ports where supported; implies diagnostics")
    parser.add_argument("--command", action="append", help="Select a supported tool name; repeatable; implies diagnostics")
    try:
        args = parser.parse_args(argv)
        if args.sample_size < 0:
            raise UsageError("--sample-size must be non-negative")
        if args.directory is not None:
            try:
                mode = Path(args.directory).lstat().st_mode
            except (FileNotFoundError, NotADirectoryError):
                raise UsageError("--directory does not exist") from None
            if not stat.S_ISDIR(mode):
                raise UsageError("--directory must be a directory, not a file or symlink")
        if args.diagnose or args.versions or args.ports or args.command:
            facts = yourself.diagnose(directory=args.directory, include_host=args.include_host,
                                      versions=args.versions, ports=args.ports,
                                      commands=args.command)
        else:
            facts = yourself.collect(directory=args.directory, sample_size=args.sample_size,
                                     include_host=args.include_host)
    except UsageError as error:
        return _error("invalid_input", str(error), 2, json_mode)
    except ValueError:
        return _error("invalid_input", "Unsupported observation request; check --command and --directory", 2, json_mode)
    except OSError:
        return _error("execution_failed", "Observation failed: a resource is unavailable or access was denied", 1, json_mode)
    partial = (facts.get("directory") and facts["directory"]["errors"] or
               facts.get("workspace") and facts["workspace"]["errors"] or
               facts.get("listeners") and facts["listeners"]["errors"] or
               any(t["status"] in ("timeout", "execution_error", "nonzero_exit",
                                   "unrecognized_version") for t in facts.get("commands", [])))
    if partial:
        return _error("partial_result", "Some observations failed; see report status fields", 3, json_mode, facts)
    render = yourself.to_json if args.format == "json" else yourself.to_markdown
    print(render(facts), end="")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        result = _main(argv)
        sys.stdout.flush()
        return result
    except (OSError, ValueError, TypeError):
        # Rendering and stream errors must not expose paths or exception text.
        try:
            result = _error("execution_failed", "Could not render or write the report", 1,
                            _json_requested(argv))
            sys.stdout.flush()
            return result
        except (OSError, ValueError, TypeError):
            # Python flushes stdout again at shutdown. Redirect the failed fd so
            # that buffered failures cannot override exit 1 with exit 120.
            try:
                with open(os.devnull, "w") as sink:
                    os.dup2(sink.fileno(), sys.stdout.fileno())
            except (OSError, ValueError, AttributeError):
                pass
            try:
                print("error: execution_failed: Could not write the report", file=sys.stderr)
            except (OSError, ValueError):
                pass
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
