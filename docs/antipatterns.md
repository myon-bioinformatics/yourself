# Anti-patterns

- **Runtime/test dependency mixing:** Keep development packages in `tests/requirements.txt`; the copied artifact must import with only the standard library.
- **Working-directory import assumptions:** CLI wrappers locate the artifact relative to `__file__`. Verify from an unrelated working directory and copy the artifact alone into a temporary project.
- **False completeness claims:** Report errors, skipped inputs and truncation separately from successful observations. Tests must exercise these boundaries.
- **Inference presented as measurement:** Return observed facts only. Search results do not prove project behavior, and directory names do not establish project or Git identity.
- **Observation triggering bootstrap:** Some version commands can initialize/update tools. Keep Flutter/Dart/npm/goma presence-only; opt-in version probes use a fixed list and shell=False, never caller-supplied argv.
- **Error interpreted as absence:** Preserve missing/timeout/nonzero/error/unsupported observations separately from successful empty observations.
- **Git identity duplication:** Tool version presence and workspace markers are environment facts; commit/branch/provenance remain with the canonical repository metadata producer.
- **One-platform discovery:** Browser, engine and runtime facts are not found the same way everywhere. Windows installs under `%ProgramFiles%` / `%LOCALAPPDATA%` (Chrome, Edge), macOS under `/Applications/*.app` (Safari, Chrome, Firefox), and Linux on `PATH` under several names (`chromium`, `chromium-browser`, `google-chrome`, `microsoft-edge`, `firefox`, `firefox-esr`) or as Snap/Flatpak. A change that adds such a fact covers Linux, macOS and Windows (and WSL, which is Linux with a Windows host), with a test per platform. A platform it cannot observe reports `measured=false` / `unsupported_platform` rather than looking absent. Engine (Blink, Gecko, WebKit) follows from the product found, not from the host OS.
