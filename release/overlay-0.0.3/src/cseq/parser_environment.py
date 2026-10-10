from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata, util
import json
from pathlib import Path
import platform
import sys
from typing import Any


_LOCK_PATH = Path(__file__).with_name("parser_dependency_lock.json")


@dataclass(frozen=True)
class ParserEnvironment:
    dependency_profile: str
    tree_sitter_available: bool
    tree_sitter_c_available: bool
    tree_sitter_version: str | None
    tree_sitter_c_version: str | None
    expected_tree_sitter_version: str
    expected_tree_sitter_c_version: str
    versions_match_lock: bool
    api_smoke_tested: bool
    api_smoke_ok: bool | None
    grammar_abi: int | None
    expected_grammar_abi: int
    grammar_abi_match: bool | None
    platform_lock_key: str | None
    runtime_wheel_filename: str | None
    runtime_wheel_sha256: str | None
    grammar_wheel_filename: str | None
    grammar_wheel_sha256: str | None
    artifact_lock_complete: bool
    artifact_lock_warnings: tuple[str, ...]
    canonical_fast_frontend_ready: bool
    fallback_required: bool
    incompatibility_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_parser_dependency_lock() -> dict[str, Any]:
    return json.loads(_LOCK_PATH.read_text(encoding="utf-8"))


def expected_parser_profile(lock: dict[str, Any] | None = None) -> dict[str, object]:
    lock = lock or load_parser_dependency_lock()
    profiles = lock.get("compatibility_profiles", {})
    if sys.version_info < (3, 9):
        legacy = profiles.get("py38")
        if not legacy:
            raise RuntimeError("Python 3.8 parser dependency profile is missing")
        return {
            "name": "py38-legacy",
            "tree_sitter_version": str(legacy["tree-sitter"]),
            "tree_sitter_c_version": str(legacy["tree-sitter-c"]),
            "grammar_abi": int(legacy["grammar_abi"]),
            "artifact_lock_source": None,
        }
    if sys.version_info < (3, 10):
        legacy = profiles.get("py39")
        if not legacy:
            raise RuntimeError("Python 3.9 parser dependency profile is missing")
        return {
            "name": "py39-legacy",
            "tree_sitter_version": str(legacy["tree-sitter"]),
            "tree_sitter_c_version": str(legacy["tree-sitter-c"]),
            "grammar_abi": int(legacy["grammar_abi"]),
            "artifact_lock_source": None,
        }
    return {
        "name": "modern",
        "tree_sitter_version": str(lock["packages"]["tree-sitter"]["version"]),
        "tree_sitter_c_version": str(lock["packages"]["tree-sitter-c"]["version"]),
        "grammar_abi": int(lock["packages"]["tree-sitter-c"]["grammar_abi"]),
        "artifact_lock_source": "packages",
    }

def _distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _platform_keys() -> tuple[str | None, str | None]:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "aarch64"
    else:
        arch = machine
    py = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    if system == "linux":
        return f"{py}-linux-{arch}-glibc", f"abi3-linux-{arch}-glibc"
    if system == "windows":
        return f"{py}-windows-{arch}", f"abi3-windows-{arch}"
    return None, None


def _artifact(lock: dict[str, Any], package: str, key: str | None) -> tuple[str | None, str | None]:
    if key is None:
        return None, None
    entry = lock["packages"][package].get("artifacts", {}).get(key)
    if not entry:
        return None, None
    return entry.get("filename"), entry.get("sha256")


def _smoke_tree_sitter(expected_abi: int, smoke_source: str, smoke_root: str) -> tuple[bool, int | None, str | None]:
    """Import the locked API and execute a minimal C parse.

    The function is called only when both distributions are discoverable.  Any
    native/API incompatibility becomes a diagnostic reason rather than leaking
    an import exception out of `cseq doctor`.
    """
    try:
        import tree_sitter  # type: ignore
        import tree_sitter_c  # type: ignore

        raw_language = tree_sitter_c.language()
        try:
            language = tree_sitter.Language(raw_language)
        except TypeError:
            language = tree_sitter.Language(raw_language, "c")
        abi = int(getattr(language, "abi_version", getattr(language, "version", -1)))
        try:
            parser = tree_sitter.Parser()
            if hasattr(parser, "set_language"):
                parser.set_language(language)
            else:
                parser.language = language
        except Exception:
            parser = tree_sitter.Parser(language)
        tree = parser.parse(smoke_source.encode("utf-8"))
        root_type = getattr(tree.root_node, "type", None)
        ok = abi == expected_abi and root_type == smoke_root
        reason = None if ok else f"smoke-mismatch: abi={abi}, root={root_type!r}"
        return ok, abi, reason
    except Exception as exc:  # pragma: no cover - depends on native runtime
        return False, None, f"api-smoke-failed: {type(exc).__name__}: {exc}"


def probe_parser_environment() -> ParserEnvironment:
    """Inspect the parser runtime against the G2 dependency lock.

    Runtime readiness is determined by the pinned package versions plus a real
    API/grammar smoke parse. Artifact hashes remain useful for reproducible/offline
    installation, but lack of a pre-recorded wheel hash must not make an already
    working runtime unusable on a new CPython version.
    """
    lock = load_parser_dependency_lock()
    profile = expected_parser_profile(lock)
    expected_ts = str(profile["tree_sitter_version"])
    expected_c = str(profile["tree_sitter_c_version"])
    expected_abi = int(profile["grammar_abi"])

    ts = util.find_spec("tree_sitter") is not None
    tsc = util.find_spec("tree_sitter_c") is not None
    ts_version = _distribution_version("tree-sitter") if ts else None
    c_version = _distribution_version("tree-sitter-c") if tsc else None
    versions_match = ts_version == expected_ts and c_version == expected_c

    runtime_key, grammar_key = _platform_keys()
    if profile["artifact_lock_source"] == "packages":
        runtime_filename, runtime_hash = _artifact(lock, "tree-sitter", runtime_key)
        grammar_filename, grammar_hash = _artifact(lock, "tree-sitter-c", grammar_key)
    else:
        runtime_filename = runtime_hash = None
        grammar_filename = grammar_hash = None

    reasons: list[str] = []
    if not ts:
        reasons.append("tree-sitter-not-installed")
    if not tsc:
        reasons.append("tree-sitter-c-not-installed")
    if ts and ts_version != expected_ts:
        reasons.append(f"tree-sitter-version-mismatch:{ts_version}!={expected_ts}")
    if tsc and c_version != expected_c:
        reasons.append(f"tree-sitter-c-version-mismatch:{c_version}!={expected_c}")
    artifact_warnings: list[str] = []
    if runtime_key is not None and runtime_filename is None:
        artifact_warnings.append(f"runtime-wheel-not-locked-for:{runtime_key}")
    if grammar_key is not None and grammar_filename is None:
        artifact_warnings.append(f"grammar-wheel-not-locked-for:{grammar_key}")

    smoke_tested = bool(ts and tsc and versions_match)
    smoke_ok: bool | None = None
    grammar_abi: int | None = None
    grammar_abi_match: bool | None = None
    if smoke_tested:
        smoke_ok, grammar_abi, smoke_reason = _smoke_tree_sitter(
            expected_abi,
            str(lock["expected_api"]["smoke_source"]),
            str(lock["expected_api"]["smoke_root_type"]),
        )
        grammar_abi_match = grammar_abi == expected_abi if grammar_abi is not None else False
        if smoke_reason:
            reasons.append(smoke_reason)

    ready = bool(
        ts
        and tsc
        and versions_match
        and smoke_tested
        and smoke_ok
        and grammar_abi_match
    )
    artifact_lock_complete = bool(runtime_filename and grammar_filename)
    return ParserEnvironment(
        dependency_profile=str(profile["name"]),
        tree_sitter_available=ts,
        tree_sitter_c_available=tsc,
        tree_sitter_version=ts_version,
        tree_sitter_c_version=c_version,
        expected_tree_sitter_version=expected_ts,
        expected_tree_sitter_c_version=expected_c,
        versions_match_lock=versions_match,
        api_smoke_tested=smoke_tested,
        api_smoke_ok=smoke_ok,
        grammar_abi=grammar_abi,
        expected_grammar_abi=expected_abi,
        grammar_abi_match=grammar_abi_match,
        platform_lock_key=runtime_key,
        runtime_wheel_filename=runtime_filename,
        runtime_wheel_sha256=runtime_hash,
        grammar_wheel_filename=grammar_filename,
        grammar_wheel_sha256=grammar_hash,
        artifact_lock_complete=artifact_lock_complete,
        artifact_lock_warnings=tuple(artifact_warnings),
        canonical_fast_frontend_ready=ready,
        fallback_required=not ready,
        incompatibility_reasons=tuple(reasons),
    )
