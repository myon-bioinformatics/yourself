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

- Read-only OS/runtime facts using stdlib APIs; no shell, network requests, package installation, service start/stop or build execution. Fixed version-only subprocesses require `versions=True` / `--versions`.
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

## Compact environment diagnosis (0.2)

The original purpose is to give Copilot/other assistants a short environment introduction, so they do not repeatedly explore the same host. `collect()` stays minimal; `diagnose()` adds measured context with explicit scope.

| API | Observation |
| --- | --- |
| `command_inventory(names=None, versions=False)` | Presence of fixed Python/Git/Docker/Node/compiler/build/search tools, including goma/gomacc. |
| `workspace_facts(path)` | Immediate project-marker presence (Python/Node/Flutter/Docker/CMake/GN/goma), plus disk byte counts; no file-content reads. |
| `listener_ports()` | Linux proc TCP LISTEN ports only; no address/PID output or network connections. Other platforms explicitly report unsupported. |
| `diagnose(...)` | OS/runtime, selected distro ID/version, tools and optional workspace/listener facts. |

```python
import yourself

print(yourself.to_markdown(yourself.diagnose()))
# Caller explicitly authorizes fixed version observations and workspace scope:
print(yourself.to_json(yourself.diagnose(directory=".", versions=True)))
```

```sh
python scripts/yourself_cli.py --diagnose
python scripts/yourself_cli.py --diagnose --directory . --versions --command python --command docker
python scripts/yourself_cli.py --diagnose --ports --format json
```

Default diagnosis discovers tool presence without executing tools. `--versions` permits only the built-in fixed version argv, with `shell=False`, stdin disabled and timeout; numeric versions alone are reported. Missing tools, timeout, nonzero exit, execution error and unrecognized versions stay distinct. Command paths, environment values and raw stdout/stderr are omitted. CLI returns 2 for partial observation errors, while missing tools and intentional presence-only observations are ordinary facts.

Flutter/Dart/npm/pytest/goma/gomacc are **presence-only**, even with `--versions`, because their entry points can bootstrap or initialize state. Docker uses `docker --version` only; no daemon query or container execution. Version commands require trusted PATH binaries; a substituted executable can have arbitrary behavior. A probe captures subprocess output in memory before considering at most its first 8192 bytes for numeric version extraction; this is not an isolated runner or a hostile-output memory limit.

Workspace marker presence does not prove buildability or installed dependencies. Symlink markers are ignored. Listener observation reads Linux proc locally; Windows/macOS return `measured=false` with `unsupported_platform`, and proc permissions/partial reads remain errors. There is no port scan, remote request, package enumeration, configuration dump, service repair, Redis/Celery integration or Git-identity collection.
