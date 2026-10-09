import json

from cseq.cli import main
from cseq.parser_environment import load_parser_dependency_lock, probe_parser_environment


def test_parser_dependency_lock_is_explicit_and_hash_pinned():
    lock = load_parser_dependency_lock()
    assert lock["schema_version"] == 1
    assert lock["packages"]["tree-sitter"]["version"] == "0.26.0"
    assert lock["packages"]["tree-sitter-c"]["version"] == "0.24.2"
    assert lock["packages"]["tree-sitter-c"]["grammar_abi"] == 15
    for package in ("tree-sitter", "tree-sitter-c"):
        for artifact in lock["packages"][package]["artifacts"].values():
            assert artifact["filename"].endswith(".whl")
            assert len(artifact["sha256"]) == 64


def test_parser_environment_reports_lock_compatibility():
    env = probe_parser_environment()
    assert env.expected_tree_sitter_version == "0.26.0"
    assert env.expected_tree_sitter_c_version == "0.24.2"
    assert env.expected_grammar_abi == 15
    assert env.fallback_required is (not env.canonical_fast_frontend_ready)
    if not env.tree_sitter_available:
        assert env.tree_sitter_version is None
        assert "tree-sitter-not-installed" in env.incompatibility_reasons
    if not env.tree_sitter_c_available:
        assert env.tree_sitter_c_version is None
        assert "tree-sitter-c-not-installed" in env.incompatibility_reasons
    if env.canonical_fast_frontend_ready:
        assert env.versions_match_lock
        assert env.api_smoke_tested
        assert env.api_smoke_ok
        assert env.grammar_abi_match


def test_current_platform_has_locked_wheel_metadata():
    env = probe_parser_environment()
    # The project currently supports/validates the Linux/Windows x86-64 paths
    # used by the development and user environments. Other platforms may report
    # an explicit not-locked reason instead of being silently accepted.
    if env.platform_lock_key in {"cp313-linux-x86_64-glibc", "cp313-windows-x86_64"}:
        assert env.runtime_wheel_filename
        assert env.runtime_wheel_sha256
        assert env.grammar_wheel_filename
        assert env.grammar_wheel_sha256


def test_doctor_exposes_parser_migration_state(capsys):
    assert main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["canonical_fast_frontend"] == "tree-sitter-c"
    env = payload["parser_environment"]
    assert "canonical_fast_frontend_ready" in env
    assert "expected_tree_sitter_version" in env
    assert "expected_tree_sitter_c_version" in env
    assert "expected_grammar_abi" in env
    assert "incompatibility_reasons" in env
    assert payload["current_execution_mode"] in {
        "tree-sitter+clang",
        "tree-sitter-syntax-only",
        "lexical-fallback+clang",
        "lexical-fallback-only",
    }



def test_doctor_reports_missing_clang_without_command_failure(monkeypatch, capsys):
    import cseq.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: None if name == "clang" else None)
    assert cli.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["semantic_backend_ready"] is False
    assert payload["clang"] is None
    assert payload["current_execution_mode"] in {"tree-sitter-syntax-only", "lexical-fallback-only"}


def test_offline_requirements_match_locked_versions_and_hashes():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements-g2-parser.txt").read_text(encoding="utf-8")
    lock = load_parser_dependency_lock()
    assert f'tree-sitter=={lock["packages"]["tree-sitter"]["version"]}' in requirements
    assert f'tree-sitter-c=={lock["packages"]["tree-sitter-c"]["version"]}' in requirements
    for package in ("tree-sitter", "tree-sitter-c"):
        for artifact in lock["packages"][package]["artifacts"].values():
            assert f'--hash=sha256:{artifact["sha256"]}' in requirements


def test_missing_future_python_wheel_lock_does_not_block_working_runtime(monkeypatch):
    import cseq.parser_environment as pe

    monkeypatch.setattr(pe.util, "find_spec", lambda name: object())
    monkeypatch.setattr(pe, "_distribution_version", lambda name: "0.26.0" if name == "tree-sitter" else "0.24.2")
    monkeypatch.setattr(pe, "_platform_keys", lambda: ("cp999-windows-x86_64", "abi3-windows-x86_64"))
    monkeypatch.setattr(pe, "_artifact", lambda lock, package, key: (None, None) if package == "tree-sitter" else ("grammar.whl", "a" * 64))
    monkeypatch.setattr(pe, "_smoke_tree_sitter", lambda *args: (True, 15, None))

    env = pe.probe_parser_environment()
    assert env.canonical_fast_frontend_ready is True
    assert env.fallback_required is False
    assert env.artifact_lock_complete is False
    assert "runtime-wheel-not-locked-for:cp999-windows-x86_64" in env.artifact_lock_warnings
    assert "runtime-wheel-not-locked-for:cp999-windows-x86_64" not in env.incompatibility_reasons
