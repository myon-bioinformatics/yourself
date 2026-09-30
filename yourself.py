"""Read-only environment facts, using the standard library only.

No subprocess, network requests, package installation or project-file reads.
Import is inert. Directory observations are shallow and explicitly requested.
"""

import json
import os
import platform
import stat
import sys
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["collect", "directory_summary", "to_json", "to_markdown"]


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
    return "\n".join(rows) + "\n"
