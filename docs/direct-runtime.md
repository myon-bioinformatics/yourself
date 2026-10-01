# Standalone runtime design

yourself.py is a daily environment-introduction tool and reusable stdlib-only
artifact. It is not a test fixture or setup script. The guarded main is part
of the single file, while import/collect remain inert/minimal.

Default: measured OS/Python facts, fixed PATH tool presence, shallow project
markers and disk capacity, plus marker-specific readiness questions.
Tool presence is not a successful build, valid credentials or a running daemon.
Guidance does not run commands, install dependencies, read project contents
or infer Git identity. No pytest/JUnit environment is required.

Optional --versions keeps the existing fixed version allowlist; new tools
remain presence-only. Optional --minimal limits output to OS/runtime.
The old advanced CLI/API retain their compatibility. Tests exercise ordinary
runtime behavior including copied standalone python -S execution.
