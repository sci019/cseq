#!/usr/bin/env python3
"""Fail closed when cseq loses established Python support.

This check is intentionally stdlib-only so Python 3.8 runners can execute it.
The full CI matrix then performs actual install, doctor, pytest and docs checks.
"""
import json
import re
import sys
from pathlib import Path

SUPPORTED_FLOOR = (3, 8)
FIXED_MINORS = tuple(range(8, 16))


def require(ok, message):
    if not ok:
        raise SystemExit("PYTHON_COMPAT_POLICY_FAIL: " + message)


def main():
    require(len(sys.argv) == 2, "usage: verify_python_compatibility.py <assembled_source>")
    source = Path(sys.argv[1])
    project_path = source / "pyproject.toml"
    require(project_path.is_file(), "assembled source lacks pyproject.toml")
    project = project_path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^requires-python\s*=\s*"([^"]+)"', project)
    require(match is not None, "Requires-Python metadata missing")
    requires_python = match.group(1)
    lower = re.search(r">=\s*(\d+)\.(\d+)", requires_python)
    require(lower is not None, "cannot confirm inclusive Python lower bound")
    require(tuple(map(int, lower.groups())) <= SUPPORTED_FLOOR,
            "Requires-Python dropped Python 3.8: " + requires_python)
    # No upper bounds which exclude any of the established 3.8..3.15 versions.
    for upper in re.finditer(r"(<=|<)\s*(\d+)\.(\d+)", requires_python):
        op, maj, minor = upper.groups()
        limit = (int(maj), int(minor))
        for n in FIXED_MINORS:
            v = (3, n)
            require((v < limit if op == "<" else v <= limit),
                    "Requires-Python upper bound excludes Python 3." + str(n))

    ver = re.search(r'(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"', project)
    require(ver is not None, "project version missing")
    version = ver.group(1)

    repo = Path(__file__).resolve().parent.parent
    overlays = []
    for folder in (repo / "release").glob("overlay-*"):
        if folder.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", folder.name[len("overlay-"):]):
            overlays.append((tuple(map(int, folder.name[len("overlay-"):].split("."))), folder))
    require(overlays, "no versioned overlays found")
    latest_version = ".".join(map(str, max(overlays)[0]))
    require(version == latest_version,
            "assembled source version %s differs from latest overlay %s" %
            (version, latest_version))

    required_markers = (
        "python_version < '3.9'",
        "python_version >= '3.9' and python_version < '3.10'",
        "python_version >= '3.10'",
    )
    for marker in required_markers:
        require(marker in project, "legacy dependency profile marker missing: " + marker)
    require(project.count('"tree-sitter==') >= 3,
            "tree-sitter legacy/current dependency profiles unexpectedly removed")
    require(project.count('"tree-sitter-c==') >= 3,
            "tree-sitter-c legacy/current dependency profiles unexpectedly removed")

    workflow = (repo / ".github" / "workflows" / "python-compat.yml").read_text(encoding="utf-8")
    matrix = re.search(r"(?m)^\s*python-version:\s*\[([^\]\n]+)\]", workflow)
    require(matrix is not None, "compatibility CI matrix missing")
    configured = set(re.findall(r'["\'](\d+\.\d+)["\']', matrix.group(1)))
    established = set("3." + str(n) for n in FIXED_MINORS)
    require(established.issubset(configured),
            "CI matrix lost Python versions: " + ",".join(sorted(established - configured)))
    require('python-version: "3.x"' in workflow, "latest stable tracking lane removed")

    print(json.dumps({"result": "PASS", "current_cseq_version": version,
                      "requires_python": requires_python,
                      "established_python": sorted(established),
                      "latest_stable_tracking": True},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
