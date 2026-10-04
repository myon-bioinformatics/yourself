# Original intent and scope

Provide a compact, measured introduction to the current execution environment for Copilot or another assistant, reducing repeated environment exploration and token use.

- Read-only observation; no repair, service start, build, install or remote communication.
- Standard-library-only single-file artifact, inert import and guarded direct CLI.
- Selected tool presence, explicitly requested fixed version observations, workspace-marker and storage facts, optional local listener-port observation.
- Version observations use shell=False with bounded time and never return raw tool stdout/stderr or environment values.
- Docker and goma/C++ tool context are relevant; Redis/Celery-specific integration is excluded.
- Git identity and provenance remain the responsibility of the canonical repository metadata producer.

The default path is presence-only. Version commands are opt-in and restricted to a fixed registry. Tools known to bootstrap/initialize are presence-only. Listener-port observation currently supports Linux proc; other platforms explicitly report unsupported rather than claiming no listeners.

Reference screenshots and earlier descriptions informed these requirements. This implementation does not copy unavailable screenshot code or introduce an unrestricted command runner.
