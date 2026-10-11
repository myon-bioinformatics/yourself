"""Read-only environment facts, using the standard library only.

No network requests, package installation or project-file content reads.
Fixed version commands are available only through explicit opt-in diagnostics.
Import is inert. Directory observations are shallow and explicitly requested.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import stat
import sys
from pathlib import Path

__version__ = "0.4.0"
__all__ = ["collect", "directory_summary", "to_json", "to_markdown", "to_text",
           "command_inventory", "workspace_facts", "listener_ports", "diagnose",
           "network_facts", "container_facts", "probe", "probe_all", "hosts_facts", "main"]


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


def listener_ports(*, proc_root='/proc'):
    """Read Linux proc TCP LISTEN ports only, never connect or expose addresses.

    Unsupported platforms return measured=False. No port scan, PID ownership,
    UDP inference, remote connections or network commands. Restricted proc data
    is represented as an error; an empty observed table is distinct.
    """
    if platform.system() != 'Linux':
        return {'measured': False, 'tcp_ports': [], 'errors': ['unsupported_platform']}
    ports, errors, observed = set(), [], 0
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
                    else:
                        errors.append('malformed_' + name)
            observed += 1
        except (OSError, UnicodeError) as error:
            errors.append(name + ':' + type(error).__name__)
    return {'measured': observed > 0, 'tcp_ports': sorted(ports),
            'errors': sorted(set(errors))}


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
             "errors": []}
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


def hosts_facts(*, etc_root="/etc"):
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
        return {"measured": False, "error": type(error).__name__, "names": found}
    return {"measured": True, "names": found}


def _gib(value):
    return "%.1f GiB" % (value / 2 ** 30)


def to_text(facts):
    """One "key: value" line per fact; tools as true/false. Easy to grep and paste."""
    def clean(value):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        return "".join("\\u%04x" % ord(c) if ord(c) < 32 or ord(c) == 127 else c for c in text)
    lines = []
    put = lambda key, value: lines.append(key + ": " + clean(value))
    if facts.get("cwd"):
        put("cwd", facts["cwd"])
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
        if network["errors"]:
            put("network_errors", ", ".join(network["errors"]))
    hosts = facts.get("hosts")
    if hosts is not None:
        for name, addresses in hosts["names"].items():
            if addresses or name == "localhost":
                put("hosts." + name, ", ".join(addresses) or "(not in /etc/hosts)")
    for name, result in (facts.get("probes") or {}).items():
        if result["ok"]:
            value = "true"
            if result["lines"]:
                value += " " + ("; ".join(result["lines"]) if len(result["lines"]) <= 10
                                else "; ".join(result["lines"][:10]) + "; ... (%d)" % len(result["lines"]))
        else:
            value = "false (" + result.get("error", "failed") + ")"
        put(name, value)
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
    args = parser.parse_args(argv)
    try:
        facts = collect() if args.minimal else diagnose(
            directory=args.directory, versions=args.versions)
    except (OSError, ValueError) as error:
        print(type(error).__name__, file=sys.stderr)
        return 2
    if not args.minimal:
        facts["cwd"] = str(Path(args.directory).absolute())
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
