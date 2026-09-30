# yourself

Single-file, standard-library Python tool for read-only inspection of host, OS, and runtime facts. Produces concise, copyable environment reports for troubleshooting and AI context.

## Design

The canonical artifact is `yourself.py`: copy it to `vendor/yourself.py` without pip or supporting modules. Like `markdown` and `ascii_artist`, it provides functions, `__all__`, and `__version__`; there is no main/CLI in the artifact and import is inert. The optional CLI lives in `scripts/yourself_cli.py`.

Python target: 3.10–3.14. Test dependencies are isolated in `tests/requirements.txt`; runtime is standard-library only.

## Public API

| Function | Contract |
| --- | --- |
| `collect(...)` | OS and Python runtime observations; optional host name and immediate directory summary. |
| `directory_summary(path, sample_size=10)` | Immediate file/directory/symlink/other counts, sorted bounded name sample and entry errors. |
| `to_json(facts)` | Sorted Unicode JSON, rejecting non-finite numbers. |
| `to_markdown(facts)` | Concise report with control characters and table/HTML delimiters escaped. |

```python
import yourself

facts = yourself.collect()
print(yourself.to_markdown(facts))
print(yourself.to_json(yourself.collect(directory=".", sample_size=5)))
# Include the hostname only when needed:
print(yourself.to_json(yourself.collect(include_host=True)))
```

## Supported / unsupported

- Read-only OS/runtime facts using stdlib APIs; no shell/subprocess, network requests, package installation, service start/stop or build execution.
- Directory observation is opt-in, shallow, includes hidden names, never reads file contents and counts symlinks without following them. Symlink roots are rejected. Individual entry errors are reported; invalid/unreadable roots raise.
- A sorted sample is deterministic for the same directory snapshot. Environment values can change. Filesystem races are not isolated.
- Default output omits host name and directory. Opt-in names/paths can identify a machine or project; choose what to include before sharing.
- No environment-variable dump, username/IP collection, recursive inventory, project inference, dependency inspection or Git identity. Repository metadata belongs to the canonical producer, not this module.

## CLI

```sh
python scripts/yourself_cli.py
python scripts/yourself_cli.py --format json --directory . --sample-size 5
python scripts/yourself_cli.py --include-host
```

Outputs only to stdout; error diagnostics go to stderr. Exit code 0 = successful observations, 2 = invalid input or directory errors. No report file is written automatically.

## Tests

```sh
python -m pip install -r tests/requirements.txt
python -m pytest --cov=yourself --cov-branch --cov-report=term-missing --cov-fail-under=90
```

Tests cover host opt-in, no content reads, shallow counts/sorted sample bounds, symlinks, entry/root errors, Unicode/control-safe reports, JSON round-trip, CLI execution from another directory and independent vendoring. CI runs Python 3.10–3.14 on Linux and Python 3.12 on Windows/macOS.
