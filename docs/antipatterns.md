# Anti-patterns

- **Runtime/test dependency mixing:** Keep development packages in `tests/requirements.txt`; the copied artifact must import with only the standard library.
- **Working-directory import assumptions:** CLI wrappers locate the artifact relative to `__file__`. Verify from an unrelated working directory and copy the artifact alone into a temporary project.
- **False completeness claims:** Report errors, skipped inputs and truncation separately from successful observations. Tests must exercise these boundaries.
- **Inference presented as measurement:** Return observed facts only. Search results do not prove project behavior, and directory names do not establish project or Git identity.
- **Observation triggering bootstrap:** Some version commands can initialize/update tools. Keep Flutter/Dart/npm/goma presence-only; opt-in version probes use a fixed list and shell=False, never caller-supplied argv.
- **Error interpreted as absence:** Preserve missing/timeout/nonzero/error/unsupported observations separately from successful empty observations.
- **Git identity duplication:** Tool version presence and workspace markers are environment facts; commit/branch/provenance remain with the canonical repository metadata producer.
