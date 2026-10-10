from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
import gc
import re
from typing import Any, Iterable

from .fast_parser import FastEligibility
from .model import (
    CallSiteRecord,
    Diagnostic,
    FileParseArtifact,
    FunctionRecord,
    ParseStatus,
    SourceRange,
    TranslationUnit,
)
from .parser import ParserBackend, _preprocessor_guard_regions
from .parser_environment import expected_parser_profile, load_parser_dependency_lock


@dataclass(frozen=True, slots=True)
class TreeSitterPrepared:
    source_bytes: bytes
    node_types: frozenset[str]
    eligibility: FastEligibility
    has_error_or_missing: bool
    root_type: str
    artifact: FileParseArtifact


class TreeSitterCParser(ParserBackend):
    """Canonical fast C syntax front-end backed by tree-sitter-c.

    This backend deliberately owns syntax only. Constructs that require C type
    or compiler semantics are surfaced through ``eligibility.reasons`` so the
    HybridParser can route them to selective Clang refinement. The syntax tree
    itself is still usable for fail-soft evidence when Clang is unavailable.
    """

    name = "tree-sitter-c-v1"

    def __init__(self) -> None:
        try:
            from tree_sitter import Language, Parser, Query
            try:
                from tree_sitter import QueryCursor
            except ImportError:
                QueryCursor = None
            import tree_sitter_c
        except Exception as exc:  # pragma: no cover - exercised on wheel env
            raise RuntimeError("locked tree-sitter runtime is not available") from exc

        language = Language(tree_sitter_c.language())
        lock = load_parser_dependency_lock()
        expected_abi = int(expected_parser_profile(lock)["grammar_abi"])
        observed_abi = int(getattr(language, "abi_version", getattr(language, "version", -1)))
        if observed_abi != expected_abi:
            raise RuntimeError(
                f"tree-sitter-c ABI mismatch: observed={observed_abi} expected={expected_abi}"
            )
        self.language = language
        self.parser = Parser(language)
        self._QueryCursor = QueryCursor
        self._function_query = Query(language, "(function_definition declarator: (function_declarator declarator: (identifier) @name) @decl) @fn")
        self._syntax_evidence_query = Query(
            language,
            "[(function_definition) (call_expression) (return_statement) "
            "(break_statement) (if_statement) (while_statement) (for_statement) "
            "(field_expression) (struct_specifier) (type_definition) (preproc_ifdef)] @node",
        )
        self._all_call_query = Query(language, "(call_expression) @call")
        self._direct_call_query = Query(language, "(call_expression function: (identifier)) @call")
        self._call_detail_query = Query(language, "(call_expression function: (_) @target) @call")

    def _query_captures(self, query, root) -> dict[str, list]:
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
        if translation_unit.source_text is not None:
            source = translation_unit.source_text.encode("utf-8")
        else:
            source = translation_unit.source.path.read_bytes()
        source_text = source.decode("utf-8", errors="replace")
        line_starts = _byte_line_starts(source)
        guarded_regions = _preprocessor_guard_regions(
            source, translation_unit.source.project_relative_path
        )

        tree = self.parser.parse(source)
        root = tree.root_node
        syntax_captures = self._query_captures(self._syntax_evidence_query, root)
        evidence_nodes = syntax_captures.get("node", ())
        all_call_captures = self._query_captures(self._all_call_query, root)
        all_calls = all_call_captures.get("call", ())
        direct_call_captures = self._query_captures(self._direct_call_query, root)
        direct_calls = direct_call_captures.get("call", ())
        function_matches = self._query_matches(self._function_query, root)
        call_matches = self._query_matches(self._call_detail_query, root)

        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        raw_functions: list[tuple] = []
        raw_calls: list[tuple] = []
        raw_problems: list[tuple] = []
        try:
            node_types_set: set[str] = {root.type}
            node_types_set.update(node.type for node in evidence_nodes)
            node_types = frozenset(node_types_set)
            indirect_call_seen = len(direct_calls) != len(all_calls)
            has_error_or_missing = bool(root.has_error)
            root_type = root.type

            reasons: list[str] = []
            if node_types.intersection({"type_definition", "struct_specifier", "field_expression"}):
                reasons.append("TYPE_AMBIGUOUS")
            if re.search(rb"\b__(?:[A-Za-z_]\w*)\b", source):
                reasons.append("COMPILER_EXTENSION")
            if re.search(rb"(?m)^\s*#\s*(?:define|if|ifdef|ifndef|elif|else|endif)\b", source):
                reasons.append("PREPROCESSOR_SEMANTICS")
            if re.search(rb"\(\s*\*\s*[A-Za-z_]\w*\s*\)\s*\(", source):
                reasons.append("INDIRECT_CALL")
            if indirect_call_seen:
                reasons.append("INDIRECT_CALL")
            eligibility = FastEligibility(not reasons, tuple(dict.fromkeys(reasons)))

            fn_spans: list[tuple[int, int, str, str]] = []
            for _pattern_index, captures in function_matches:
                fn_node = captures["fn"][0]
                name_node = captures["name"][0]
                declarator = captures["decl"][0]
                fn_start, fn_end = fn_node.start_byte, fn_node.end_byte
                name_start, name_end = name_node.start_byte, name_node.end_byte
                decl_start, decl_end = declarator.start_byte, declarator.end_byte
                name = source[name_start:name_end].decode("utf-8", errors="replace")
                prefix = source[fn_start:decl_start].decode("utf-8", errors="replace")
                sm = re.search(r"\b(static|extern)\b", prefix)
                storage = sm.group(1) if sm else None
                type_qualifier = prefix.strip() or None
                decl_text = source[decl_start:decl_end].decode("utf-8", errors="replace")
                params = _parameter_names_from_declarator_text(decl_text)
                name_line, name_col = _line_col_from_byte(name_start, line_starts)
                qid = _function_id(
                    translation_unit, name, name_line, name_col, storage, type_qualifier
                )
                raw_functions.append(
                    (name, qid, storage, type_qualifier, params, fn_start, fn_end)
                )
                fn_spans.append((fn_start, fn_end, qid, name))

            fn_starts = [x[0] for x in fn_spans]
            for _pattern_index, captures in call_matches:
                call_node = captures["call"][0]
                target = captures["target"][0]
                call_start, call_end = call_node.start_byte, call_node.end_byte
                pos = bisect_right(fn_starts, call_start) - 1
                if pos < 0 or call_end > fn_spans[pos][1]:
                    continue
                _fs, _fe, caller_id, caller_name = fn_spans[pos]
                callee = (
                    source[target.start_byte:target.end_byte].decode("utf-8", errors="replace")
                    if target.type == "identifier" else None
                )
                raw_calls.append(
                    (
                        caller_id, caller_name, callee,
                        source[call_start:call_end].decode("utf-8", errors="replace"),
                        call_start, call_end, callee is None,
                        _control_context(call_node, source),
                    )
                )

            if has_error_or_missing:
                for node in _walk(root):
                    is_problem = (
                        node.type == "ERROR"
                        or bool(getattr(node, "is_error", False))
                        or bool(getattr(node, "is_missing", False))
                    )
                    if is_problem:
                        raw_problems.append(
                            (
                                node.start_byte, node.end_byte, node.type,
                                bool(getattr(node, "is_missing", False)),
                                source[node.start_byte:node.end_byte].decode("utf-8", errors="replace"),
                            )
                        )
        finally:
            captures = None
            fn_node = name_node = declarator = call_node = target = node = None
            call_matches = call_detail_cursor = None
            function_matches = function_cursor = None
            evidence_nodes = syntax_captures = syntax_cursor = None
            all_calls = all_call_captures = all_call_cursor = None
            direct_calls = direct_call_captures = direct_call_cursor = None
            root = tree = None
            if gc_was_enabled:
                gc.enable()
        if gc_was_enabled:
            gc.collect()

        functions = [
            FunctionRecord(
                name=name, qualified_id=qid,
                source_path=translation_unit.source.project_relative_path,
                source_range=_range_from_bytes(start, end, line_starts),
                storage_class=storage, type_qualifier=type_qualifier, parameters=params,
            )
            for name, qid, storage, type_qualifier, params, start, end in raw_functions
        ]
        callsites = [
            CallSiteRecord(
                caller_id=caller_id, caller_name=caller_name, callee_name=callee,
                raw_text=raw_text, source_path=translation_unit.source.project_relative_path,
                source_range=_range_from_bytes(start, end, line_starts),
                unknown_possible=unknown, control_context=context,
            )
            for caller_id, caller_name, callee, raw_text, start, end, unknown, context in raw_calls
        ]
        diagnostics: list[Diagnostic] = []
        opaque_regions: list[SourceRange] = []
        for start, end, node_type, missing, raw in raw_problems:
            opaque_regions.append(_range_from_bytes(start, end, line_starts))
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    message=f"tree-sitter syntax recovery node type={node_type} missing={missing}",
                    raw=raw,
                )
            )
        status = (
            ParseStatus.ERROR_RECOVERED if has_error_or_missing and (functions or callsites)
            else ParseStatus.PARTIAL if has_error_or_missing
            else ParseStatus.OK
        )
        artifact = FileParseArtifact(
            translation_unit=translation_unit, status=status, functions=functions,
            callsites=callsites, diagnostics=diagnostics, opaque_regions=opaque_regions,
            guarded_regions=guarded_regions, parser_backend=self.name,
        )
        return TreeSitterPrepared(
            source_bytes=source, node_types=node_types, eligibility=eligibility,
            has_error_or_missing=has_error_or_missing, root_type=root_type, artifact=artifact,
        )

    def parse(self, translation_unit: TranslationUnit) -> FileParseArtifact:
        prepared = self.prepare(translation_unit)
        if not prepared.eligibility.eligible:
            raise ValueError(
                "semantic refinement required: " + ", ".join(prepared.eligibility.reasons)
            )
        return self.parse_prepared(translation_unit, prepared)

    def parse_prepared(
        self, translation_unit: TranslationUnit, prepared: TreeSitterPrepared
    ) -> FileParseArtifact:
        # ``prepare`` closes the native Tree-sitter lifetime boundary and stores
        # only pure Python evidence/artifacts. No Node/Cursor crosses this API.
        return prepared.artifact


def _walk(root: Any) -> Iterable[Any]:
    # TreeCursor avoids materialising each node's full ``children`` tuple.
    # That is both cheaper and avoids a Windows py-tree-sitter access-violation
    # observed on large translation units with hundreds of function siblings.
    cursor = root.walk()
    while True:
        yield cursor.node
        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue
        while True:
            if not cursor.goto_parent():
                return
            if cursor.goto_next_sibling():
                break


def _text(source: bytes, node: Any | None) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _range(node: Any) -> SourceRange:
    return SourceRange(
        node.start_point.row + 1,
        node.start_point.column + 1,
        node.end_point.row + 1,
        node.end_point.column + 1,
    )


def _declarator_identifier(node: Any | None) -> Any | None:
    if node is None:
        return None
    if node.type == "identifier":
        return node
    # C declarator nesting is represented through a named `declarator` field.
    child = node.child_by_field_name("declarator")
    if child is not None:
        found = _declarator_identifier(child)
        if found is not None:
            return found
    # Conservative fallback for parenthesized/pointer declarators. Avoid the
    # parameter list so parameter identifiers are not mistaken for function names.
    for candidate in getattr(node, "named_children", ()) or ():
        if candidate.type == "parameter_list":
            continue
        found = _declarator_identifier(candidate)
        if found is not None:
            return found
    return None


def _parameter_names(source: bytes, declarator: Any | None) -> tuple[str, ...]:
    if declarator is None:
        return ()
    parameter_list = next((n for n in _walk(declarator) if n.type == "parameter_list"), None)
    if parameter_list is None:
        return ()
    out: list[str] = []
    for child in getattr(parameter_list, "named_children", ()) or ():
        if child.type != "parameter_declaration":
            continue
        decl = child.child_by_field_name("declarator")
        ident = _declarator_identifier(decl)
        if ident is not None:
            out.append(_text(source, ident))
    return tuple(out)


def _byte_line_starts(source: bytes) -> tuple[int, ...]:
    starts = [0]
    starts.extend(i + 1 for i, b in enumerate(source) if b == 10)
    return tuple(starts)


def _line_col_from_byte(offset: int, line_starts: tuple[int, ...]) -> tuple[int, int]:
    line_index = bisect_right(line_starts, offset) - 1
    return line_index + 1, offset - line_starts[line_index] + 1


def _range_from_bytes(start: int, end: int, line_starts: tuple[int, ...]) -> SourceRange:
    sl, sc = _line_col_from_byte(start, line_starts)
    el, ec = _line_col_from_byte(end, line_starts)
    return SourceRange(sl, sc, el, ec)


def _parameter_names_from_declarator_text(declarator: str) -> tuple[str, ...]:
    left = declarator.find("(")
    right = declarator.rfind(")")
    if left < 0 or right <= left:
        return ()
    text = declarator[left + 1:right].strip()
    if not text or text == "void":
        return ()
    names: list[str] = []
    for part in text.split(","):
        ids = re.findall(r"[A-Za-z_]\w*", part)
        if not ids:
            continue
        candidate = ids[-1]
        if candidate not in {"void", "char", "short", "int", "long", "float", "double", "signed", "unsigned", "const", "volatile", "struct", "union", "enum"}:
            names.append(candidate)
    return tuple(names)


def _storage_class(source: bytes, function_node: Any, declarator: Any | None = None) -> str | None:
    # Avoid named_children enumeration here: Windows py-tree-sitter 0.26.0
    # can access-violate on large sibling sets. Storage-class keywords occur
    # before the function declarator, so the raw prefix is sufficient.
    end = declarator.start_byte if declarator is not None else function_node.end_byte
    prefix = source[function_node.start_byte:end].decode("utf-8", errors="replace")
    match = re.search(r"\b(static|extern)\b", prefix)
    return match.group(1) if match else None


def _type_qualifier(source: bytes, function_node: Any, declarator: Any | None) -> str | None:
    if declarator is None:
        return None
    prefix = source[function_node.start_byte : declarator.start_byte].decode("utf-8", errors="replace").strip()
    return prefix or None


def _function_id(
    tu: TranslationUnit,
    name: str,
    line: int,
    col: int,
    storage: str | None,
    qual_type: str | None,
) -> str:
    return f"{tu.source.project_relative_path}:{line}:{col}:{storage or 'external'}:{name}:{qual_type or '?'}"


def _control_context(node: Any, source: bytes) -> tuple[str, ...]:
    contexts: list[str] = []
    child = node
    parent = getattr(node, "parent", None)
    while parent is not None and parent.type != "function_definition":
        if parent.type == "if_statement":
            condition = parent.child_by_field_name("condition")
            cond = " ".join(_text(source, condition).split()) if condition is not None else ""
            base = f"IF:if{cond}" if cond.startswith("(") else f"IF:if({cond})"
            alt = parent.child_by_field_name("alternative")
            branch = "/else" if alt is not None and _node_contains(alt, child) else "/then"
            contexts.append(base + branch)
        elif parent.type in {"while_statement", "for_statement", "do_statement"}:
            raw = " ".join(_text(source, parent).split())
            condition = parent.child_by_field_name("condition")
            if parent.type == "while_statement":
                cond = " ".join(_text(source, condition).split()) if condition is not None else ""
                header = f"while {cond}" if cond.startswith("(") else f"while ({cond})"
                compact = "".join(header.split()).lower()
                contexts.append("LOOP:" + (("[infinite] " if compact in {"while(1)", "while(true)"} else "") + header))
            elif parent.type == "for_statement":
                header = raw.split("{",1)[0].strip()
                compact = "".join(header.split()).lower()
                contexts.append("LOOP:" + (("[infinite] " if compact == "for(;;)" else "") + header))
            else:
                compact = "".join(raw.split()).lower()
                infinite = compact.endswith("while(1);") or compact.endswith("while(true);")
                contexts.append("LOOP:" + (("[infinite] " if infinite else "") + ("do/while(1)" if infinite else "do/while")))
        elif parent.type == "case_statement":
            labels = _tree_sitter_case_labels(parent, source)
            contexts.append("DEFAULT" if labels == ["DEFAULT"] else "CASE:" + "|".join(labels))
        elif parent.type == "switch_statement":
            condition = parent.child_by_field_name("condition")
            cond = " ".join(_text(source, condition).split()) if condition is not None else ""
            contexts.append(f"SWITCH:switch{cond}" if cond.startswith("(") else f"SWITCH:switch({cond})")
        child = parent
        parent = getattr(parent, "parent", None)
    contexts.reverse()
    return tuple(contexts)


def _node_contains(ancestor: Any, node: Any) -> bool:
    return ancestor.start_byte <= node.start_byte and node.end_byte <= ancestor.end_byte


def _tree_sitter_case_labels(case_node: Any, source: bytes) -> list[str]:
    value = case_node.child_by_field_name("value")
    if value is None:
        return ["DEFAULT"]
    labels = [_text(source, value).strip() or "?"]
    parent = getattr(case_node, "parent", None)
    if parent is None:
        return labels
    named = list(getattr(parent, "named_children", ()))
    try:
        pos = named.index(case_node)
    except ValueError:
        return labels
    i = pos - 1
    prefix: list[str] = []
    while i >= 0 and named[i].type == "case_statement":
        prev = named[i]
        prev_value = prev.child_by_field_name("value")
        # A label-only case has no named child other than its value.  It falls
        # through to the current case and therefore represents an alternative.
        if prev_value is None or len(getattr(prev, "named_children", ())) != 1:
            break
        prefix.append(_text(source, prev_value).strip() or "?")
        i -= 1
    prefix.reverse()
    return prefix + labels
