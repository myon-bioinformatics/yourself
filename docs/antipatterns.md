# Anti-patterns

- **Runtime/test dependency mixing:** Keep development packages in `tests/requirements.txt`; the copied artifact must import with only the standard library.
- **Working-directory import assumptions:** CLI wrappers locate the artifact relative to `__file__`. Verify from an unrelated working directory and copy the artifact alone into a temporary project.
- **False completeness claims:** Report errors, skipped inputs and truncation separately from successful observations. Tests must exercise these boundaries.
- **Inference presented as measurement:** Return observed facts only. Search results do not prove project behavior, and directory names do not establish project or Git identity.
