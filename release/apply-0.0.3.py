from __future__ import annotations

from pathlib import Path
import json

ROOT = Path("source")
SRC = ROOT / "src" / "cseq"


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label}: expected source fragment not found")
    return text.replace(old, new)


p = ROOT / "pyproject.toml"
s = p.read_text(encoding="utf-8")
s = replace_required(s, 'version = "0.0.2"', 'version = "0.0.3"', "version")
s = replace_required(s, 'requires-python = ">=3.10"', 'requires-python = ">=3.9"', "requires-python")
s = replace_required(
    s,
    'parser-tree-sitter = ["tree-sitter==0.26.0", "tree-sitter-c==0.24.2"]',
    """parser-tree-sitter = [
  "tree-sitter==0.23.2; python_version < '3.10'",
  "tree-sitter-c==0.21.4; python_version < '3.10'",
  "tree-sitter==0.26.0; python_version >= '3.10'",
  "tree-sitter-c==0.24.2; python_version >= '3.10'",
]""",
    "parser dependencies",
)
p.write_text(s, encoding="utf-8")

p = SRC / "__init__.py"
s = p.read_text(encoding="utf-8")
s = replace_required(s, '__version__ = "0.0.2"', '__version__ = "0.0.3"', "runtime version")
p.write_text(s, encoding="utf-8")

(SRC / "_compat.py").write_text(
    """from __future__ import annotations

from dataclasses import dataclass as _stdlib_dataclass
import sys


def dataclass(*args, **kwargs):
    \"\"\"Version-compatible stdlib dataclass decorator.

    Python 3.9 does not accept the slots keyword. On Python 3.10 and newer the
    keyword is preserved so existing cseq memory behavior remains unchanged.
    \"\"\"
    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
    return _stdlib_dataclass(*args, **kwargs)
""",
    encoding="utf-8",
)

for path in SRC.glob("*.py"):
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("from dataclasses import ") and "dataclass" in line:
            names = [x.strip() for x in line.split("import ", 1)[1].split(",")]
            if "dataclass" in names:
                remain = [x for x in names if x != "dataclass"]
                if remain:
                    out.append("from dataclasses import " + ", ".join(remain))
                out.append("from ._compat import dataclass")
                continue
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")

p = SRC / "parser_dependency_lock.json"
lock = json.loads(p.read_text(encoding="utf-8"))
lock["compatibility_profiles"] = {
    "py39": {
        "python_spec": ">=3.9,<3.10",
        "tree-sitter": "0.23.2",
        "tree-sitter-c": "0.21.4",
        "grammar_abi": 14,
        "artifact_policy": "runtime-capability-validated; wheel hashes pending preservation",
    }
}
p.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

p = SRC / "parser_environment.py"
s = p.read_text(encoding="utf-8")
s = replace_required(
    s,
    "class ParserEnvironment:\n    tree_sitter_available: bool",
    "class ParserEnvironment:\n    dependency_profile: str\n    tree_sitter_available: bool",
    "ParserEnvironment profile field",
)
s = replace_required(
    s,
    "def _distribution_version(name: str) -> str | None:",
    """def expected_parser_profile(lock: dict[str, Any] | None = None) -> dict[str, object]:
    lock = lock or load_parser_dependency_lock()
    if sys.version_info < (3, 10):
        legacy = lock.get("compatibility_profiles", {}).get("py39")
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


def _distribution_version(name: str) -> str | None:""",
    "profile helper",
)
s = replace_required(
    s,
    """    ts_lock = lock["packages"]["tree-sitter"]
    c_lock = lock["packages"]["tree-sitter-c"]
    expected_ts = str(ts_lock["version"])
    expected_c = str(c_lock["version"])
    expected_abi = int(c_lock["grammar_abi"])""",
    """    profile = expected_parser_profile(lock)
    expected_ts = str(profile["tree_sitter_version"])
    expected_c = str(profile["tree_sitter_c_version"])
    expected_abi = int(profile["grammar_abi"])""",
    "profile selection",
)
s = replace_required(
    s,
    """    runtime_key, grammar_key = _platform_keys()
    runtime_filename, runtime_hash = _artifact(lock, "tree-sitter", runtime_key)
    grammar_filename, grammar_hash = _artifact(lock, "tree-sitter-c", grammar_key)""",
    """    runtime_key, grammar_key = _platform_keys()
    if profile["artifact_lock_source"] == "packages":
        runtime_filename, runtime_hash = _artifact(lock, "tree-sitter", runtime_key)
        grammar_filename, grammar_hash = _artifact(lock, "tree-sitter-c", grammar_key)
    else:
        runtime_filename = runtime_hash = None
        grammar_filename = grammar_hash = None""",
    "artifact profile",
)
s = replace_required(
    s,
    """    return ParserEnvironment(
        tree_sitter_available=ts,""",
    """    return ParserEnvironment(
        dependency_profile=str(profile["name"]),
        tree_sitter_available=ts,""",
    "profile output",
)
p.write_text(s, encoding="utf-8")

p = SRC / "tree_sitter_parser.py"
s = p.read_text(encoding="utf-8-sig")
s = replace_required(
    s,
    "from .parser_environment import load_parser_dependency_lock",
    "from .parser_environment import expected_parser_profile, load_parser_dependency_lock",
    "parser environment import",
)
s = replace_required(
    s,
    """            from tree_sitter import Language, Parser, Query, QueryCursor
            import tree_sitter_c""",
    """            from tree_sitter import Language, Parser, Query
            try:
                from tree_sitter import QueryCursor
            except ImportError:
                QueryCursor = None
            import tree_sitter_c""",
    "QueryCursor import",
)
s = replace_required(
    s,
    'expected_abi = int(lock["packages"]["tree-sitter-c"]["grammar_abi"])',
    'expected_abi = int(expected_parser_profile(lock)["grammar_abi"])',
    "ABI profile",
)
s = replace_required(
    s,
    "    def prepare(self, translation_unit: TranslationUnit) -> TreeSitterPrepared:\n",
    """    def _query_captures(self, query, root) -> dict[str, list]:
        if self._QueryCursor is not None:
            raw = self._QueryCursor(query).captures(root)
        else:
            raw = query.captures(root)
        if isinstance(raw, dict):
            return {str(name): list(nodes) for name, nodes in raw.items()}
        grouped: dict[str, list] = {}
        for node, name in raw:
            grouped.setdefault(str(name), []).append(node)
        return grouped

    def _query_matches(self, query, root):
        if self._QueryCursor is not None:
            return self._QueryCursor(query).matches(root)
        return query.matches(root)

    def prepare(self, translation_unit: TranslationUnit) -> TreeSitterPrepared:
""",
    "query adapter methods",
)
s = replace_required(
    s,
    """        syntax_cursor = self._QueryCursor(self._syntax_evidence_query)
        syntax_captures = syntax_cursor.captures(root)
        evidence_nodes = syntax_captures.get("node", ())
        all_call_cursor = self._QueryCursor(self._all_call_query)
        all_call_captures = all_call_cursor.captures(root)
        all_calls = all_call_captures.get("call", ())
        direct_call_cursor = self._QueryCursor(self._direct_call_query)
        direct_call_captures = direct_call_cursor.captures(root)
        direct_calls = direct_call_captures.get("call", ())
        function_cursor = self._QueryCursor(self._function_query)
        function_matches = function_cursor.matches(root)
        call_detail_cursor = self._QueryCursor(self._call_detail_query)
        call_matches = call_detail_cursor.matches(root)
""",
    """        syntax_captures = self._query_captures(self._syntax_evidence_query, root)
        evidence_nodes = syntax_captures.get("node", ())
        all_call_captures = self._query_captures(self._all_call_query, root)
        all_calls = all_call_captures.get("call", ())
        direct_call_captures = self._query_captures(self._direct_call_query, root)
        direct_calls = direct_call_captures.get("call", ())
        function_matches = self._query_matches(self._function_query, root)
        call_matches = self._query_matches(self._call_detail_query, root)
""",
    "query execution",
)
p.write_text(s, encoding="utf-8")

p = ROOT / "tests" / "test_parser_environment.py"
s = p.read_text(encoding="utf-8")
s = replace_required(
    s,
    "from cseq.parser_environment import load_parser_dependency_lock, probe_parser_environment",
    "from cseq.parser_environment import expected_parser_profile, load_parser_dependency_lock, probe_parser_environment",
    "test profile import",
)
s = replace_required(
    s,
    """    assert env.expected_tree_sitter_version == "0.26.0"
    assert env.expected_tree_sitter_c_version == "0.24.2"
    assert env.expected_grammar_abi == 15
""",
    """    profile = expected_parser_profile()
    assert env.expected_tree_sitter_version == profile["tree_sitter_version"]
    assert env.expected_tree_sitter_c_version == profile["tree_sitter_c_version"]
    assert env.expected_grammar_abi == profile["grammar_abi"]
""",
    "test profile assertions",
)
p.write_text(s, encoding="utf-8")

print("Applied cseq 0.0.3 Python 3.9 compatibility overlay")
