# Standalone entry-point design

The stdlib-only single file is a daily-use artifact, like markdown/ascii_artist.
main only parses arguments, invokes existing public functions, formats output
and maps results to exit codes. It introduces no separate analysis subsystem.
Import is inert. Direct execution works with python -S from another directory.
Tests validate the artifact; they are not a required runtime environment.
Existing API limits, privacy rules, exclusions and caveats remain applicable.
JUnit/pytest are optional existing integrations, not a setup prerequisite.
