"""Read-only environment facts, using the standard library only.

No network requests, package installation or project-file content reads.
Fixed version commands are available only through explicit opt-in diagnostics.
Import is inert. Directory observations are shallow and explicitly requested.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import stat
import sys
from pathlib import Path

__version__ = "0.6.0"
__all__ = ["collect", "directory_summary", "to_json", "to_markdown", "to_text",
           "command_inventory", "workspace_facts", "listener_ports", "diagnose",
           "network_facts", "container_facts", "probe", "probe_all", "hosts_facts", "no_proxy_covers",
           "resource_facts", "session_facts", "runner_fingerprint", "runner_fingerprint_summary",
           "main"]


def directory_summary(path, *, sample_size=10):
    """Count immediate entry types and return a sorted, bounded name sample.

    No recursion or file-content reads. Symlinks are counted without following
    them. Invalid roots raise; individual entry errors are reported. Paths and
    names can be sensitive; callers choose whether to include this observation.
    """
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 0:
        raise ValueError("sample_size must be a non-negative integer")
    path = Path(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("path must be a directory, not a symlink")
    counts = {"files": 0, "directories": 0, "symlinks": 0, "other": 0}
    names = []
    errors = []
    with os.scandir(path) as entries:
        for entry in entries:
            # Keep only the lexicographically smallest sample, not all names.
            if sample_size:
                names.append(entry.name)
                names.sort()
                del names[sample_size:]
            try:
                if entry.is_symlink():
                    kind = "symlinks"
                elif entry.is_dir(follow_symlinks=False):
                    kind = "directories"
                elif entry.is_file(follow_symlinks=False):
                    kind = "files"
                else:
                    kind = "other"
                counts[kind] += 1
            except OSError as error:
                errors.append({"name": entry.name, "reason": type(error).__name__})
    return {"path": str(path.absolute()), "counts": counts, "sample": names,
            "errors": sorted(errors, key=lambda item: item["name"])}


def collect(*, directory=None, sample_size=10, include_host=False):
    """Observe OS/runtime facts; optional host name and shallow directory data.

    No environment variable dump, username, IP, Git identity or inferred project
    facts. Host name is opt-in to keep the default report easy to share.
    Stable key order; values reflect the current environment, not a fixed fixture.
    """
    facts = {"schema_version": 1,
             "os": {"system": platform.system(), "release": platform.release(),
                    "machine": platform.machine()},
             "runtime": {"implementation": platform.python_implementation(),
                         "version": platform.python_version(),
                         "bits": 64 if sys.maxsize > 2 ** 32 else 32},
             "host": {"name": platform.node()} if include_host else None,
             "directory": None}
    if directory is not None:
        facts["directory"] = directory_summary(directory, sample_size=sample_size)
    return facts


def to_json(facts):
    """Serialize facts as deterministic Unicode JSON; reject non-finite numbers."""
    return json.dumps(facts, ensure_ascii=False, sort_keys=True, indent=2,
                      allow_nan=False) + "\n"


def _cell(value):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join("\\u%04x" % ord(c) if ord(c) < 32 or ord(c) == 127 else c
                   for c in text).replace("|", "&#124;").replace("`", "&#96;")


def to_markdown(facts):
    """Render collect() facts as a concise Markdown report without raw controls."""
    rows = ["# Environment", "", "| Fact | Observed value |", "| --- | --- |"]
    for section in ("os", "runtime", "host"):
        for key, value in (facts.get(section) or {}).items():
            rows.append("| " + _cell(section + "." + key) + " | " + _cell(value) + " |")
    directory = facts.get("directory")
    if directory is not None:
        rows.extend(["", "## Directory (immediate entries only)", "",
                     "Path: " + _cell(directory["path"]), "",
                     "Counts: " + _cell(directory["counts"]), "",
                     "Sample: " + _cell(directory["sample"]), "",
                     "Errors: " + _cell(directory["errors"])])
    if "commands" in facts:
        rows.extend(["", "## Tools", "", "| Tool | Observation |", "| --- | --- |"])
        for tool in facts["commands"]:
            observed = tool["version"] or ("available; " + tool["status"] if tool["available"] else "not found")
            rows.append("| " + _cell(tool["name"]) + " | " + _cell(observed) + " |")
    for key in ("os_release", "workspace", "listeners"):
        if facts.get(key) is not None:
            rows.extend(["", key + ": " + _cell(facts[key])])
    return "\n".join(rows) + "\n"


# Fixed observation-only commands: no shell, arbitrary argv, service startup,
# builds, package installation, Docker daemon access, or compiler invocation.
_COMMANDS = {
    'python': ('--version',), 'git': ('--version',), 'docker': ('--version',),
    'node': ('--version',), 'cmake': ('--version',), 'ninja': ('--version',),
    'gcc': ('--version',), 'g++': ('--version',), 'clang': ('--version',),
    'rg': ('--version',),
    # Some CLIs (Flutter/Dart/npm/goma) can bootstrap/update or initialize state.
    # Discover their presence, but do not run them as an environment probe.
    'flutter': None, 'dart': None, 'npm': None, 'pytest': None,
    'goma': None, 'gomacc': None,
    'gh': None, 'uv': None, 'pip': None, 'pipx': None,
    'npx': None, 'pnpm': None, 'yarn': None, 'playwright': None,
    'actionlint': None, 'jq': None, 'curl': None,
    'ffmpeg': None, 'magick': None, 'tsc': None,
    # Presence only: what this machine can do (GPU, languages, browsers,
    # clipboard, cloud/database CLIs). Never executed by this module.
    'nvidia-smi': None, 'make': None, 'java': None, 'go': None, 'rustc': None,
    'cargo': None, 'deno': None, 'bun': None, 'chromium': None,
    'google-chrome': None, 'firefox': None, 'xclip': None, 'xsel': None,
    'wl-copy': None, 'pbcopy': None, 'sqlite3': None, 'psql': None,
    'kubectl': None, 'terraform': None, 'aws': None, 'gcloud': None,
    'ssh': None, 'wget': None, 'zip': None, 'unzip': None, 'tar': None,
}
_MARKERS = ('pyproject.toml', 'requirements.txt', 'package.json', 'pubspec.yaml',
            'Dockerfile', 'compose.yaml', 'docker-compose.yml', 'CMakeLists.txt',
            'BUILD.gn', 'WORKSPACE', '.goma')


def command_inventory(names=None, *, versions=False, timeout=2):
    """Find fixed tool names; version execution is explicit opt-in.

    Runs only fixed version argv with shell=False, stdin disabled, no cwd
    override and a timeout. Reports numeric versions, never command output,
    paths, environment values, stdout or stderr. PATH executables must be trusted;
    a malicious/replaced binary cannot be made read-only by its argv.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise ValueError('timeout must be a finite number in (0, 30]')
    if isinstance(names, str):
        raise TypeError('names must be a sequence, not a string')
    names = tuple(_COMMANDS) if names is None else tuple(names)
    if any(not isinstance(n, str) or n not in _COMMANDS for n in names):
        raise ValueError('unsupported command name')
    rows = []
    for name in sorted(set(names)):
        executable = sys.executable if name == 'python' else shutil.which(name)
        item = {'name': name, 'available': bool(executable), 'version': None,
                'status': 'not_found' if not executable else 'not_requested'}
        args = _COMMANDS[name]
        if executable and versions:
            if args is None:
                item['status'] = 'presence_only'
            else:
                try:
                    observed = subprocess.run([executable, *args], shell=False,
                                              stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              timeout=timeout, check=False)
                    # Only a numeric version escapes the observation boundary.
                    output = (observed.stdout + b'\n' + observed.stderr)[:8192]
                    version = re.search(rb'(?<![\w.])v?(\d+(?:\.\d+){1,3})(?![\w.])', output)
                    if observed.returncode:
                        item['status'] = 'nonzero_exit'
                    elif version:
                        item['version'] = version.group(1).decode('ascii')
                        item['status'] = 'measured'
                    else:
                        item['status'] = 'unrecognized_version'
                except subprocess.TimeoutExpired:
                    item['status'] = 'timeout'
                except OSError:
                    item['status'] = 'execution_error'
        rows.append(item)
    return rows


def workspace_facts(path):
    """Observe project marker presence and disk capacity without content reads.

    No Git identity, dependency parsing or recursive inventory. Marker files are
    lstat-ed only; symlinks do not count as files. Disk facts are byte counts.
    """
    root = Path(path)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('workspace must be a directory, not a symlink')
    markers, errors = [], []
    for name in _MARKERS:
        try:
            mode = (root / name).lstat().st_mode
            if stat.S_ISREG(mode) or (name == '.goma' and stat.S_ISDIR(mode)):
                markers.append(name)
        except FileNotFoundError:
            continue
        except OSError as error:
            errors.append({'marker': name, 'reason': type(error).__name__})
    try:
        disk = shutil.disk_usage(root)
        storage = {'total_bytes': disk.total, 'used_bytes': disk.used,
                   'free_bytes': disk.free}
    except OSError as error:
        storage = None
        errors.append({'marker': 'storage', 'reason': type(error).__name__})
    return {'markers': markers, 'storage': storage, 'errors': errors}


def _proc_address(text, family):
    """Decode a /proc/net/tcp{,6} local address (host byte order per 32-bit word)."""
    import ipaddress
    raw = bytes.fromhex(text)
    if family == 'tcp':
        return str(ipaddress.IPv4Address(raw[::-1]))
    words = b''.join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
    if words[:12] == b'\x00' * 10 + b'\xff\xff':
        return '::ffff:' + str(ipaddress.IPv4Address(words[12:]))
    return str(ipaddress.IPv6Address(words))


def listener_ports(*, proc_root='/proc', addresses=False):
    """Read Linux proc TCP LISTEN ports; never connect, scan or name processes.

    With addresses=True also return each local bind address, so "listens on
    127.0.0.1 only" versus "all interfaces" is visible (a frequent localhost /
    container port-mapping problem). Bind addresses are local facts.

    Unsupported platforms return measured=False. No port scan, PID ownership,
    UDP inference, remote connections or network commands. Restricted proc data
    is represented as an error; an empty observed table is distinct.
    """
    if platform.system() != 'Linux':
        result = {'measured': False, 'tcp_ports': [], 'errors': ['unsupported_platform']}
        if addresses:
            result['bindings'] = []
        return result
    ports, errors, observed, bindings = set(), [], 0, set()
    for name in ('tcp', 'tcp6'):
        try:
            with (Path(proc_root) / 'net' / name).open('r', encoding='ascii') as stream:
                next(stream, None)
                for line in stream:
                    fields = line.split()
                    if len(fields) < 4 or fields[3] != '0A':
                        continue
                    try:
                        port = int(fields[1].rsplit(':', 1)[1], 16)
                    except (ValueError, IndexError):
                        errors.append('malformed_' + name)
                        continue
                    if 0 <= port <= 65535:
                        ports.add(port)
                        if addresses:
                            try:
                                bindings.add((port, _proc_address(fields[1].rsplit(':', 1)[0], name)))
                            except ValueError:
                                errors.append('malformed_' + name)
                    else:
                        errors.append('malformed_' + name)
            observed += 1
        except (OSError, UnicodeError) as error:
            errors.append(name + ':' + type(error).__name__)
    result = {'measured': observed > 0, 'tcp_ports': sorted(ports),
              'errors': sorted(set(errors))}
    if addresses:
        result['bindings'] = [{'port': port, 'address': address} for port, address in sorted(bindings)]
    return result


def diagnose(*, directory=None, include_host=False, versions=False,
             ports=False, commands=None, timeout=2):
    """Compact AI environment context; no environment changes or repairs.

    Defaults to tool presence only. Version commands and proc listener reads
    each require their own opt-in. Workspace inspection requires a directory.
    Does not import project code, read configs, inspect Git or contact a daemon.
    """
    facts = collect(include_host=include_host)
    facts['commands'] = command_inventory(commands, versions=versions, timeout=timeout)
    facts['workspace'] = workspace_facts(directory) if directory is not None else None
    facts['listeners'] = listener_ports() if ports else None
    facts['os_release'] = None
    if platform.system() == 'Linux':
        try:
            release = platform.freedesktop_os_release()
            facts['os_release'] = {k: release[k] for k in ('ID', 'VERSION_ID') if k in release}
        except OSError:
            pass
    return facts


def _hex_ipv4(value):
    """/proc/net/route stores IPv4 little-endian hex."""
    raw = int(value, 16)
    return ".".join(str((raw >> shift) & 255) for shift in (0, 8, 16, 24))


_LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")


def no_proxy_covers(value=None):
    """Whether NO_PROXY exempts each loopback name (the value itself is never returned).

    With HTTPS_PROXY set and localhost missing from NO_PROXY, a local dev
    server is reached through the proxy and fails. Accepts "*", exact names,
    leading-dot/wildcard forms of localhost and CIDR ranges such as 127.0.0.0/8.
    """
    import ipaddress
    if value is None:
        value = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    tokens = {t.strip().lower().strip("[]") for t in value.replace(" ", ",").split(",") if t.strip()}
    covers = {}
    for name in _LOOPBACK_NAMES:
        hit = "*" in tokens or name in tokens
        if not hit and name == "localhost":
            hit = bool(tokens & {".localhost", "*.localhost"})
        if not hit and name != "localhost":
            for token in tokens:
                try:
                    hit = ipaddress.ip_address(name) in ipaddress.ip_network(token, strict=False)
                except ValueError:
                    continue
                if hit:
                    break
        covers[name] = hit
    return covers


def network_facts(*, proc_root="/proc", sys_root="/sys", etc_root="/etc"):
    """Local network configuration from files only: no packets, DNS or commands.

    Host name, interfaces with state, IPv4/IPv6 addresses, default route and
    DNS servers. Proxy settings are reported as set/unset only, because their
    values can carry credentials. Unreadable sources are listed in errors.
    """
    proc, sysfs, etc = Path(proc_root), Path(sys_root), Path(etc_root)
    facts = {"hostname": platform.node(), "interfaces": [], "ipv4": [], "ipv6": [],
             "default_route": None, "dns": {"nameservers": [], "search": []},
             "proxy_set": {name: bool(os.environ.get(name) or os.environ.get(name.lower()))
                           for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")},
             "no_proxy_covers": no_proxy_covers(), "errors": []}
    try:
        for entry in sorted((sysfs / "class" / "net").iterdir()):
            try:
                state = (entry / "operstate").read_text(encoding="ascii").strip()
            except OSError:
                state = "unknown"
            facts["interfaces"].append({"name": entry.name, "state": state})
    except OSError as error:
        facts["errors"].append("interfaces:" + type(error).__name__)
    try:
        # fib_trie lists every address; "/32 host LOCAL" marks one of ours.
        previous = None
        for line in (proc / "net" / "fib_trie").read_text(encoding="ascii").splitlines():
            match = re.search(r"\|-- (\d+\.\d+\.\d+\.\d+)$", line)
            if match:
                previous = match.group(1)
            elif "/32 host LOCAL" in line and previous and previous not in facts["ipv4"]:
                facts["ipv4"].append(previous)
    except OSError as error:
        facts["errors"].append("ipv4:" + type(error).__name__)
    try:
        for line in (proc / "net" / "if_inet6").read_text(encoding="ascii").splitlines():
            fields = line.split()
            if len(fields) == 6 and len(fields[0]) == 32:
                address = ":".join(fields[0][i:i + 4] for i in range(0, 32, 4))
                facts["ipv6"].append({"address": address, "interface": fields[5]})
    except FileNotFoundError:
        pass  # IPv6 disabled is an observation, not an error
    except OSError as error:
        facts["errors"].append("ipv6:" + type(error).__name__)
    try:
        for line in (proc / "net" / "route").read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "00000000":
                facts["default_route"] = {"interface": fields[0], "gateway": _hex_ipv4(fields[2])}
                break
    except (OSError, ValueError) as error:
        facts["errors"].append("route:" + type(error).__name__)
    try:
        for line in (etc / "resolv.conf").read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "nameserver":
                facts["dns"]["nameservers"].append(fields[1])
            elif fields and fields[0] in ("search", "domain"):
                facts["dns"]["search"].extend(fields[1:])
    except OSError as error:
        facts["errors"].append("dns:" + type(error).__name__)
    return facts


def container_facts(*, proc_root="/proc", root="/"):
    """Container / VM hints from marker files, cgroups and CPU flags only."""
    proc, base = Path(proc_root), Path(root)
    kinds = []
    if (base / ".dockerenv").exists():
        kinds.append("docker")
    if (base / "run" / ".containerenv").exists():
        kinds.append("podman")
    try:
        cgroup = (proc / "1" / "cgroup").read_text(encoding="utf-8", errors="replace")
        for marker, kind in (("docker", "docker"), ("kubepods", "kubernetes"),
                             ("containerd", "containerd"), ("lxc", "lxc")):
            if marker in cgroup and kind not in kinds:
                kinds.append(kind)
    except OSError:
        pass
    declared = os.environ.get("container")  # systemd convention (podman, lxc, ...)
    if declared and re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", declared) and declared not in kinds:
        kinds.append(declared)
    if os.environ.get("KUBERNETES_SERVICE_HOST") and "kubernetes" not in kinds:
        kinds.append("kubernetes")
    try:
        virtualized = " hypervisor" in (proc / "cpuinfo").read_text(encoding="ascii", errors="replace")
    except OSError:
        virtualized = None
    return {"in_container": bool(kinds), "kinds": kinds, "virtualized": virtualized}


# Fixed probes run by default (shell=False, bounded time and output). Their
# point is "does this work here?", so a failure is a result, not an error.
_PROBES = {
    "docker.context": ("docker", "context", "show"),
    "docker.server": ("docker", "version", "--format", "{{.Server.Version}}"),
    "docker.networks": ("docker", "network", "ls", "--format", "{{.Name}} {{.Driver}} {{.Scope}}"),
    "docker.bridge": ("docker", "network", "inspect", "bridge", "--format",
                      "{{range .IPAM.Config}}{{.Subnet}} gw={{.Gateway}} {{end}}"),
    "docker.containers": ("docker", "ps", "-a", "--format", "{{.Names}} {{.Image}} {{.State}} {{.Ports}}"),
    "docker.images": ("docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.Size}}"),
    # The address the system resolver (nsswitch, not only /etc/hosts) gives first.
    "localhost.resolves_to": ("getent", "hosts", "localhost"),
    "gpu": ("nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"),
}


def probe(argv, *, timeout=3, max_lines=20):
    """Run one fixed argv; ok is True only for exit 0. Output is bounded."""
    if not shutil.which(argv[0]):
        return {"ok": False, "lines": [], "error": "not_installed"}
    try:
        done = subprocess.run(list(argv), shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "lines": [], "error": "timeout"}
    except OSError as error:
        return {"ok": False, "lines": [], "error": type(error).__name__}
    text = done.stdout[:65536].decode("utf-8", "replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = {"ok": done.returncode == 0, "lines": lines[:max_lines], "truncated": len(lines) > max_lines}
    if done.returncode:
        first = next((l.strip() for l in done.stderr[:4096].decode("utf-8", "replace").splitlines() if l.strip()), "")
        result["error"] = first[:200] or "exit %d" % done.returncode
    return result


def probe_all(names=None, *, timeout=3):
    names = tuple(_PROBES) if names is None else tuple(names)
    if any(n not in _PROBES for n in names):
        raise ValueError("unsupported probe name")
    return {name: probe(_PROBES[name], timeout=timeout) for name in names}


def hosts_facts(*, etc_root="/etc", proc_root="/proc"):
    """What /etc/hosts maps localhost-like names to (no DNS lookup).

    'localhost' can mean 127.0.0.1 or ::1; a server bound to only one of them
    is unreachable through the other, so both mappings are shown.
    """
    wanted = ("localhost", "host.docker.internal", "gateway.docker.internal")
    found = {name: [] for name in wanted}
    try:
        for line in (Path(etc_root) / "hosts").read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split("#", 1)[0].split()
            for name in fields[1:]:
                if name in found and fields[0] not in found[name]:
                    found[name].append(fields[0])
    except OSError as error:
        return {"measured": False, "error": type(error).__name__, "names": found,
                "ipv6_loopback": _ipv6_loopback(proc_root)}
    return {"measured": True, "names": found, "ipv6_loopback": _ipv6_loopback(proc_root)}


def _ipv6_loopback(proc_root):
    """True when ::1 is configured on Linux (/proc/net/if_inet6); None off Linux.

    A missing if_inet6 on Linux means IPv6 is disabled, so ::1 cannot answer.
    """
    if platform.system() != "Linux":
        return None
    text = _read(Path(proc_root) / "net" / "if_inet6")
    if text is None:
        return False
    return any(line.split()[:1] == ["00000000000000000000000000000001"] for line in text.splitlines())


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return None


def resource_facts(*, proc_root="/proc", cgroup_root="/sys/fs/cgroup"):
    """CPU, memory, load, uptime, container (cgroup v1/v2) limits, open-file limit."""
    proc, cg = Path(proc_root), Path(cgroup_root)
    facts = {"cpus": os.cpu_count(), "usable_cpus": None, "cpu_limit": None,
             "memory_total_bytes": None, "memory_available_bytes": None, "swap_total_bytes": None,
             "memory_limit_bytes": None, "load": None, "uptime_seconds": None, "open_files_limit": None}
    if hasattr(os, "sched_getaffinity"):
        try:
            facts["usable_cpus"] = len(os.sched_getaffinity(0))
        except OSError:
            pass
    for line in (_read(proc / "meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        value = rest.split()
        if value and value[0].isdigit():
            target = {"MemTotal": "memory_total_bytes", "MemAvailable": "memory_available_bytes",
                      "SwapTotal": "swap_total_bytes"}.get(key)
            if target:
                facts[target] = int(value[0]) * 1024
    loadavg = (_read(proc / "loadavg") or "").split()
    if len(loadavg) >= 3:
        facts["load"] = loadavg[:3]
    uptime = (_read(proc / "uptime") or "").split()
    if uptime:
        try:
            facts["uptime_seconds"] = int(float(uptime[0]))
        except ValueError:
            pass
    # cgroup v2 then v1; "max" or a huge v1 sentinel means unlimited (None).
    memory = _read(cg / "memory.max") or _read(cg / "memory" / "memory.limit_in_bytes")
    if memory and memory.isdigit() and int(memory) < 2 ** 60:
        facts["memory_limit_bytes"] = int(memory)
    cpu = (_read(cg / "cpu.max") or "").split()
    quota, period = (cpu + [None, None])[:2] if cpu else (_read(cg / "cpu" / "cpu.cfs_quota_us"),
                                                         _read(cg / "cpu" / "cpu.cfs_period_us"))
    if quota and period and quota.lstrip("-").isdigit() and period.isdigit() and int(quota) > 0 and int(period) > 0:
        facts["cpu_limit"] = round(int(quota) / int(period), 2)
    try:
        import resource
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        facts["open_files_limit"] = None if soft == resource.RLIM_INFINITY else soft
    except (ImportError, OSError, ValueError):
        pass
    return facts


def session_facts():
    """Who/where/when this process runs: user, shell, locale, time zone,
    display (headless?), Python executable and virtualenv, CA bundle and CI
    variables (set/unset only)."""
    import getpass
    from datetime import datetime
    try:
        user = getpass.getuser()
    except Exception:  # no passwd entry in some containers
        user = None
    uid = os.getuid() if hasattr(os, "getuid") else None
    zone = os.environ.get("TZ") or _read("/etc/timezone")
    if not zone:
        try:
            zone = os.readlink("/etc/localtime").split("zoneinfo/", 1)[-1]
        except OSError:
            zone = None
    flags = lambda names: {name: bool(os.environ.get(name)) for name in names}
    return {"user": user, "uid": uid, "root": uid == 0 if uid is not None else None,
            "home": str(Path.home()) if os.environ.get("HOME") else None,
            "shell": Path(os.environ.get("SHELL", "")).name or None,
            "lang": os.environ.get("LC_ALL") or os.environ.get("LANG"),
            "timezone": zone, "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "display": flags(("DISPLAY", "WAYLAND_DISPLAY")),
            "python_executable": sys.executable, "virtualenv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
            "ca_env": flags(("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "CURL_CA_BUNDLE")),
            "ci": flags(("CI", "GITHUB_ACTIONS")), "writable_cwd": os.access(".", os.W_OK)}


def _duration(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    return ("%dd " % days if days else "") + "%dh%02dm" % (hours, rest // 60)


def _gib(value):
    return "%.1f GiB" % (value / 2 ** 30)


_RUNNER_ENV_KEYS = ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion",
                    "RUNNER_TOOL_CACHE", "AGENT_TOOLSDIRECTORY")
_DMI_FILES = ("sys_vendor", "product_name", "chassis_asset_tag")


def _soft_ok(value):
    return {"ok": True, "value": value}


def _soft_err(reason, **extra):
    item = {"ok": False, "reason": reason}
    item.update(extra)
    return item


def _read_soft(path):
    try:
        return _soft_ok(Path(path).read_text(encoding="utf-8", errors="replace").strip())
    except FileNotFoundError:
        return _soft_err("not_found")
    except OSError as error:
        return _soft_err(type(error).__name__)


def _env_soft(name, environ=None):
    environ = environ if environ is not None else os.environ
    if name not in environ:
        return _soft_err("unset")
    return _soft_ok(environ[name])


_IMAGEDATA_MAX_BYTES = 2 * 1024 * 1024


def _imagedata_paths(root="/"):
    base = Path(root)
    if platform.system() == "Windows":
        return [Path("C:/imagedata.json")]
    return [base / "imagegeneration" / "imagedata.json"]


def _json_load_soft(text):
    try:
        return _soft_ok(json.loads(text))
    except RecursionError:
        return _soft_err("recursion_depth")
    except json.JSONDecodeError as error:
        return _soft_err("invalid_json", detail=str(error))


def _normalize_imagedata_value(data, path):
    if isinstance(data, dict):
        safe = {key: data[key] for key in ("image", "ImageOS", "ImageVersion")
                if key in data and isinstance(data[key], str)}
        return _soft_ok({"path": str(path), "kind": "object", "keys": sorted(safe), "object": safe})
    if isinstance(data, list):
        entries = []
        for item in data:
            if isinstance(item, dict):
                entries.append({key: item[key] for key in ("group", "detail") if key in item})
            else:
                entries.append({"value_type": type(item).__name__})
        return _soft_ok({"path": str(path), "kind": "array", "length": len(data), "entries": entries})
    return _soft_err("unsupported_type", path=str(path))


def _parse_imagedata(paths):
    for path in paths:
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(_IMAGEDATA_MAX_BYTES + 1)
        except FileNotFoundError:
            continue
        except OSError as error:
            return _soft_err(type(error).__name__, path=str(path))
        if len(raw) > _IMAGEDATA_MAX_BYTES:
            return _soft_err("too_large", path=str(path), bytes=len(raw))
        loaded = _json_load_soft(raw.decode("utf-8", errors="replace"))
        if not loaded["ok"]:
            return {**loaded, "path": str(path)}
        return _normalize_imagedata_value(loaded["value"], path)
    return _soft_err("not_found", paths=[str(p) for p in paths])


def _os_identity(*, etc_root="/etc"):
    system = platform.system()
    if system == "Linux":
        release = _read_soft(Path(etc_root) / "os-release")
        if release["ok"]:
            parsed = {}
            for line in release["value"].splitlines():
                if "=" in line and not line.startswith("#"):
                    key, _, raw = line.partition("=")
                    parsed[key] = raw.strip().strip('"')
            release["value"] = parsed
        return {"platform": system, "os_release": release}
    if system == "Darwin":
        return {"platform": system, "mac_ver": _soft_ok(platform.mac_ver())}
    if system == "Windows":
        return {"platform": system, "win32_ver": _soft_ok(platform.win32_ver())}
    return {"platform": system, "detail": _soft_err("unsupported_platform")}


def _kernel_facts():
    return {"release": platform.release(), "version": platform.version()}


def _dmi_facts(*, sys_root="/sys", root="/"):
    base = Path(sys_root) / "class" / "dmi" / "id"
    fields = {name: _read_soft(base / name) for name in _DMI_FILES}
    waagent = Path(root) / "var/lib/waagent"
    try:
        waagent_present = waagent.exists()
    except OSError as error:
        waagent_present = None
        waagent_err = type(error).__name__
    else:
        waagent_err = None
    evidence = []
    for name, item in fields.items():
        if item.get("ok") and item.get("value"):
            value = item["value"].lower()
            matches = [provider for provider, tokens in (
                ("azure", ("microsoft", "azure")), ("aws", ("amazon", "ec2")),
                ("gcp", ("google",))) if any(token in value for token in tokens)]
            evidence.extend("dmi:" + name + ":" + provider for provider in matches)
    if waagent_present:
        evidence.append("path:/var/lib/waagent")
    guess = None
    blob = " ".join(item.get("value", "") for item in fields.values() if item.get("ok")).lower()
    if "microsoft" in blob or "azure" in blob or waagent_present:
        guess = "azure"
    elif "amazon" in blob or "ec2" in blob:
        guess = "aws"
    elif "google" in blob:
        guess = "gcp"
    safe_fields = {name: (_soft_ok([provider for provider in ("azure", "aws", "gcp")
                                  if "dmi:" + name + ":" + provider in evidence])
                          if item.get("ok") else item) for name, item in fields.items()}
    return {"dmi": safe_fields, "waagent": _soft_ok(waagent_present) if waagent_err is None
            else _soft_err(waagent_err), "guess": guess, "evidence": evidence}


def _dpkg_inventory(*, list_path=None, timeout=30):
    executable = shutil.which("dpkg-query")
    if not executable:
        return _soft_err("not_available")
    if platform.system() != "Linux":
        return _soft_err("unsupported_platform")
    try:
        done = subprocess.run([executable, "-W", "-f=${Package}\t${Version}\n"],
                              shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return _soft_err("timeout")
    except OSError as error:
        return _soft_err(type(error).__name__)
    if done.returncode:
        detail = done.stderr[:500].decode("utf-8", "replace").strip()
        return _soft_err("nonzero_exit", detail=detail or ("exit %d" % done.returncode))
    text = done.stdout.decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    written = None
    if list_path is not None:
        try:
            Path(list_path).write_text(text, encoding="utf-8")
            written = str(list_path)
        except OSError as error:
            return {"ok": True, "value": {"count": len(lines), "sha256": digest,
                                          "list_path": _soft_err(type(error).__name__)}}
    value = {"count": len(lines), "sha256": digest}
    if written is not None:
        value["list_path"] = _soft_ok(written)
    return _soft_ok(value)


def _tool_cache_inventory(path):
    root = Path(path)
    try:
        if not root.is_dir():
            return _soft_err("not_a_directory")
    except OSError as error:
        return _soft_err(type(error).__name__)
    tools = {}
    errors = []
    try:
        names = sorted(entry.name for entry in root.iterdir())
    except OSError as error:
        return _soft_err(type(error).__name__)
    for name in names:
        tool_dir = root / name
        try:
            if not tool_dir.is_dir():
                continue
            versions = sorted(child.name for child in tool_dir.iterdir() if child.is_dir())
            tools[name] = versions
        except OSError as error:
            errors.append({"tool": name, "reason": type(error).__name__})
    result = _soft_ok({"path": str(root.absolute()), "tools": tools})
    if errors:
        result["entry_errors"] = errors
    return result


_IMDS_COMPUTE_ALLOW = ("osType", "location", "vmSize", "sku", "offer", "version")
_IMDS_READ_BYTES = 65536
_IMDS_INSTANCE_URL = "http://169.254.169.254/metadata/instance?api-version=2021-02-01"


def _sanitize_imds_payload(data):
    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    compute = data.get("compute")
    if not isinstance(compute, dict):
        return {}
    return {"compute": {key: compute[key] for key in _IMDS_COMPUTE_ALLOW if key in compute}}


def _build_imds_opener():
    """IMDS fetcher: no HTTP proxy, no redirects."""
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _imds_facts(*, timeout=1.0):
    """Optional cloud metadata; only when caller opts in (stdlib urllib, no proxy)."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(_IMDS_INSTANCE_URL, headers={"Metadata": "true"})
    opener = _build_imds_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_IMDS_READ_BYTES)
    except urllib.error.HTTPError as error:
        return _soft_err("http_error", status=error.code)
    except urllib.error.URLError as error:
        reason = error.reason
        name = type(reason).__name__ if reason is not None else "URLError"
        return _soft_err("url_error", detail=name)
    except TimeoutError:
        return _soft_err("timeout")
    except OSError as error:
        return _soft_err(type(error).__name__)
    loaded = _json_load_soft(raw.decode("utf-8", errors="replace"))
    if not loaded["ok"]:
        return loaded
    return _soft_ok(_sanitize_imds_payload(loaded["value"]))


def runner_fingerprint(*, environ=None, root="/", etc_root="/etc", sys_root="/sys",
                       dpkg_list_path=None, imds=False, imds_timeout=1.0):
    """CI runner image fingerprint; every probe fails soft. No IMDS unless imds=True."""
    environ = environ if environ is not None else os.environ
    tool_cache = _env_soft("RUNNER_TOOL_CACHE", environ)
    if not tool_cache["ok"]:
        tool_cache = _env_soft("AGENT_TOOLSDIRECTORY", environ)
    caches = []
    if tool_cache.get("ok"):
        caches.append(_tool_cache_inventory(tool_cache["value"]))
    fingerprint = {"schema": "runner-fingerprint/1",
                   "github_actions_env": {key: _env_soft(key, environ) for key in _RUNNER_ENV_KEYS},
                   "imagedata": _parse_imagedata(_imagedata_paths(root)),
                   "os_identity": _os_identity(etc_root=etc_root),
                   "kernel": _kernel_facts(),
                   "python": {"implementation": platform.python_implementation(),
                              "version": platform.python_version(),
                              "bits": 64 if sys.maxsize > 2 ** 32 else 32},
                   "cloud": _dmi_facts(sys_root=sys_root, root=root),
                   "dpkg": _dpkg_inventory(list_path=dpkg_list_path),
                   "tool_cache": tool_cache,
                   "tool_cache_contents": caches,
                   "imds": None}
    if imds:
        fingerprint["imds"] = _imds_facts(timeout=imds_timeout)
    return fingerprint


def runner_fingerprint_summary(fingerprint):
    """Short human-readable summary for logs and GITHUB_STEP_SUMMARY."""
    lines = ["Runner fingerprint (schema runner-fingerprint/1)"]
    env = fingerprint.get("github_actions_env") or {}
    for key in ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion"):
        item = env.get(key) or {}
        value = item.get("value") if item.get("ok") else ("(" + item.get("reason", "missing") + ")")
        lines.append("%s: %s" % (key, value))
    os_id = fingerprint.get("os_identity") or {}
    if os_id.get("platform") == "Linux":
        rel = (os_id.get("os_release") or {})
        if rel.get("ok"):
            parsed = rel["value"]
            pretty = parsed.get("PRETTY_NAME") or "%s %s" % (parsed.get("ID", "?"), parsed.get("VERSION_ID", "?"))
            lines.append("os: %s" % pretty)
    cloud = fingerprint.get("cloud") or {}
    if cloud.get("guess"):
        lines.append("cloud_guess: %s (%s)" % (cloud["guess"], ", ".join(cloud.get("evidence") or []) or "no evidence"))
    else:
        lines.append("cloud_guess: (none)")
    dpkg = fingerprint.get("dpkg") or {}
    if dpkg.get("ok"):
        lines.append("dpkg_packages: %s sha256=%s" % (dpkg["value"]["count"], dpkg["value"]["sha256"][:16] + "..."))
    elif dpkg.get("reason"):
        lines.append("dpkg_packages: (%s)" % dpkg["reason"])
    cache = fingerprint.get("tool_cache_contents") or []
    if cache and cache[0].get("ok"):
        tools = cache[0]["value"]["tools"]
        lines.append("tool_cache_tools: %s" % (", ".join(sorted(tools)) if tools else "(empty)"))
    imds = fingerprint.get("imds")
    if imds is not None:
        if imds.get("ok"):
            compute = (imds.get("value") or {}).get("compute") or {}
            bits = ["%s=%s" % (key, compute[key]) for key in _IMDS_COMPUTE_ALLOW if key in compute]
            lines.append("imds: ok" + (" (" + ", ".join(bits) + ")" if bits else ""))
        else:
            lines.append("imds: (%s)" % imds.get("reason", "failed"))
    return "\n".join(lines) + "\n"


def to_text(facts):
    """One "key: value" line per fact; tools as true/false. Easy to grep and paste."""
    def clean(value):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        return "".join("\\u%04x" % ord(c) if ord(c) < 32 or ord(c) == 127 else c for c in text)
    lines = []
    put = lambda key, value: lines.append(key + ": " + clean(value))
    if facts.get("cwd"):
        put("cwd", facts["cwd"])
    session = facts.get("session")
    if session is not None:
        put("user", "%s (uid %s, root=%s)" % (session["user"], session["uid"], str(session["root"]).lower()))
        put("writable_cwd", session["writable_cwd"])
        put("shell", session["shell"] or "(unset)")
        put("lang", session["lang"] or "(unset)")
        put("timezone", session["timezone"] or "(unknown)")
        put("local_time", session["local_time"])
        put("display", any(session["display"].values()) or "false (headless)")
        put("python_executable", session["python_executable"])
        put("virtualenv", session["virtualenv"])
        for name, value in session["ca_env"].items():
            put("ca_env." + name, value)
        put("ci", " ".join("%s=%s" % (k, str(v).lower()) for k, v in session["ci"].items()))
    put("os", " ".join(str(facts["os"][k]) for k in ("system", "release", "machine")))
    if facts.get("os_release"):
        put("os_release", " ".join(facts["os_release"].values()))
    runtime = facts["runtime"]
    put("python", "%s %s %sbit" % (runtime["implementation"], runtime["version"], runtime["bits"]))
    if facts.get("host"):
        put("host", facts["host"]["name"])
    container = facts.get("container")
    if container is not None:
        put("in_container", container["in_container"])
        if container["kinds"]:
            put("container_kinds", ", ".join(container["kinds"]))
        put("virtualized", container["virtualized"])
    network = facts.get("network")
    if network is not None:
        put("hostname", network["hostname"])
        put("ipv4", ", ".join(network["ipv4"]) or "(none)")
        if network["ipv6"]:
            put("ipv6", ", ".join(x["address"] + "%" + x["interface"] for x in network["ipv6"]))
        put("interfaces", ", ".join(x["name"] + "(" + x["state"] + ")" for x in network["interfaces"]) or "(none)")
        route = network["default_route"]
        put("default_gateway", route["gateway"] + " via " + route["interface"] if route else "(none)")
        put("dns", ", ".join(network["dns"]["nameservers"]) or "(none)")
        if network["dns"]["search"]:
            put("dns_search", ", ".join(network["dns"]["search"]))
        for name, value in network["proxy_set"].items():
            put("proxy." + name, value)
        for name, value in (network.get("no_proxy_covers") or {}).items():
            put("proxy.no_proxy_covers." + name, value)
        if network["errors"]:
            put("network_errors", ", ".join(network["errors"]))
    hosts = facts.get("hosts")
    if hosts is not None:
        for name, addresses in hosts["names"].items():
            if addresses or name == "localhost":
                put("hosts." + name, ", ".join(addresses) or "(not in /etc/hosts)")
        if hosts.get("ipv6_loopback") is not None:
            put("localhost.ipv6_loopback", hosts["ipv6_loopback"])
    for name, result in (facts.get("probes") or {}).items():
        if result["ok"]:
            value = "true"
            if result["lines"]:
                value += " " + ("; ".join(result["lines"]) if len(result["lines"]) <= 10
                                else "; ".join(result["lines"][:10]) + "; ... (%d)" % len(result["lines"]))
        else:
            value = "false (" + result.get("error", "failed") + ")"
        put(name, value)
    res = facts.get("resources")
    if res is not None:
        put("cpus", "%s (usable %s, cgroup limit %s)" % (res["cpus"], res["usable_cpus"], res["cpu_limit"] or "none"))
        if res["memory_total_bytes"]:
            put("memory", "%s available of %s (cgroup limit %s, swap %s)" % (
                _gib(res["memory_available_bytes"] or 0), _gib(res["memory_total_bytes"]),
                _gib(res["memory_limit_bytes"]) if res["memory_limit_bytes"] else "none",
                _gib(res["swap_total_bytes"] or 0)))
        if res["load"]:
            put("load", " ".join(res["load"]))
        if res["uptime_seconds"] is not None:
            put("uptime", _duration(res["uptime_seconds"]))
        put("open_files_limit", res["open_files_limit"] or "unlimited")
    workspace = facts.get("workspace")
    if workspace:
        storage = workspace.get("storage")
        if storage:
            put("disk_free", _gib(storage["free_bytes"]) + " of " + _gib(storage["total_bytes"]))
        put("markers", ", ".join(workspace.get("markers", [])) or "(none)")
    for tool in facts.get("commands", []):
        value = tool.get("available")
        if tool.get("version"):
            value = "true " + tool["version"]
        put("tool." + tool["name"], value)
    listeners = facts.get("listeners")
    if listeners is not None:
        if not listeners.get("measured"):
            put("listening", "unmeasured (" + ", ".join(listeners.get("errors") or ["unknown"]) + ")")
        elif "bindings" in listeners:
            show = lambda b: ("[%s]" % b["address"] if ":" in b["address"] else b["address"]) + ":%d" % b["port"]
            put("listening", ", ".join(show(b) for b in listeners["bindings"]) or "(none)")
        else:
            put("listening_tcp_ports", ", ".join(map(str, listeners["tcp_ports"])) or "(none)")
    return "\n".join(lines) + "\n"


def main(argv=None):
    """Direct single-file entry point; no-argument use observes current workspace."""
    import argparse
    parser = argparse.ArgumentParser(description="Read-only AI environment introduction")
    parser.add_argument("--directory", default=".")
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("--versions", action="store_true")
    parser.add_argument("--minimal", action="store_true",
                        help="OS/runtime only; omit workspace and tool observations")
    parser.add_argument("--no-network", action="store_true",
                        help="omit host name, addresses, routes, DNS and container hints")
    parser.add_argument("--no-probes", action="store_true",
                        help="do not run the docker / nvidia-smi probes")
    parser.add_argument("--runner-env", action="store_true",
                        help="emit CI runner image fingerprint (offline by default)")
    parser.add_argument("--runner-env-json", action="store_true",
                        help="with --runner-env, print runner-fingerprint/1 JSON instead of summary")
    parser.add_argument("--runner-env-imds", action="store_true",
                        help="with --runner-env, also query cloud instance metadata (short timeout)")
    parser.add_argument("--runner-env-dpkg-list", metavar="PATH",
                        help="with --runner-env on Linux, write full dpkg-query list to PATH")
    parser.add_argument("--runner-env-github-summary", action="store_true",
                        help="with --runner-env, append summary to GITHUB_STEP_SUMMARY when set")
    args = parser.parse_args(argv)
    runner_flags = (args.runner_env_json or args.runner_env_imds or args.runner_env_dpkg_list
                    or args.runner_env_github_summary)
    if runner_flags and not args.runner_env:
        print("runner-env flags require --runner-env", file=sys.stderr)
        return 2
    if args.runner_env:
        fingerprint = runner_fingerprint(dpkg_list_path=args.runner_env_dpkg_list,
                                         imds=args.runner_env_imds)
        if args.runner_env_json:
            print(to_json(fingerprint), end="")
        else:
            print(runner_fingerprint_summary(fingerprint), end="")
        if args.runner_env_github_summary:
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary_path:
                try:
                    with open(summary_path, "a", encoding="utf-8") as stream:
                        stream.write("\n## Runner fingerprint\n\n")
                        stream.write(runner_fingerprint_summary(fingerprint))
                except OSError as error:
                    print("GITHUB_STEP_SUMMARY: " + type(error).__name__, file=sys.stderr)
        return 0
    try:
        facts = collect() if args.minimal else diagnose(
            directory=args.directory, versions=args.versions)
    except (OSError, ValueError) as error:
        print(type(error).__name__, file=sys.stderr)
        return 2
    if not args.minimal:
        facts["cwd"] = str(Path(args.directory).absolute())
        facts["session"] = session_facts()
        facts["resources"] = resource_facts()
        if not args.no_network:
            facts["listeners"] = listener_ports(addresses=True)
        if not args.no_network:
            facts["container"] = container_facts()
            facts["network"] = network_facts()
            facts["hosts"] = hosts_facts()
        if not args.no_probes:
            facts["probes"] = probe_all()
    render = {"json": to_json, "markdown": to_markdown, "text": to_text}[args.format]
    print(render(facts), end="")
    partial = bool(facts.get("workspace") and facts["workspace"]["errors"])
    partial |= any(row["status"] in ("timeout", "execution_error", "nonzero_exit",
                                    "unrecognized_version")
                   for row in facts.get("commands", []))
    return 2 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
