# Agent instructions

- Keep `yourself.py` a single standard-library file; observation only (no installs, no repairs, presence-only for tools whose version command can bootstrap).
- Facts that depend on the OS (browsers and their engines, runtimes, install paths, caches) cover Linux, macOS and Windows alike, plus WSL as its own case. Do not add Windows-only (or Linux-only) lookups; see "One-platform discovery" in `docs/antipatterns.md`. Each platform gets a test with fake roots or environment variables, never the real machine.
- An unsupported or unobservable platform is reported as such (`measured=false`, `unsupported_platform`), not as absent.
- Name tests `tests/test_<topic>.py` next to the existing ones and run `python -m pytest -q`.
