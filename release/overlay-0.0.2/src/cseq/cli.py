from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import __version__
from .binary import load_binary_image
from .dwarf import load_dwarf_lines
from .runtime_address import discover_runtime_address_evidence
from .project import Project
from .config import CseqConfig, default_config_text, validate_config
from .sequence import build_sequence, render_plantuml
from .html import render_html
from .html_bundle import write_html_bundle, write_html_range_bundle, write_html_range_bundle_from_event_store
from .marker import apply_marker, apply_marker_overlay, audit_markers, undo_marker
from .scanner import scan_sources
from .runtime import RuntimePattern, build_trace_session, iter_runtime_files
from .runtime_cpu import apply_runtime_cpu_evidence
from .explain import explain_call
from .query import query_calls, query_unresolved, query_paths
from .event_store import EventStore
from .trace_diff import diff_sessions
from .trace_analysis import reconstruct_static_paths, score_marker_suggestions, ReconstructionState
from .server import make_server
from .parser_environment import probe_parser_environment
from .parser_migration import build_g2b_resume_report
from .docs import export_document, export_all


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cseq")
    p.add_argument("--version", action="version", version=f"cseq {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check local execution environment")
    doctor.add_argument("--parser-wheel-dir", help="verify the locked Tree-sitter parser wheels without installing them")
    doctor.set_defaults(handler=_doctor)

    analyze = sub.add_parser("analyze", help="scan and parse a C source tree")
    analyze.add_argument("path")
    analyze.add_argument("--json", action="store_true", dest="as_json")
    analyze.add_argument("--entry", default="main")
    analyze.add_argument("--plantuml")
    analyze.add_argument("--html")
    analyze.add_argument("--html-mode", choices=["portable","bundle","range","auto"], default="auto")
    analyze.add_argument("--bundle-threshold-events", type=int, default=10000)
    analyze.add_argument("--range-threshold-events", type=int, default=100000)
    analyze.add_argument("--viewport-size", type=int, default=200)
    analyze.add_argument("--config")
    analyze.add_argument("--compile-db")
    analyze.add_argument("--linker-map")
    analyze.add_argument("--binary")
    analyze.add_argument("--dwarf")
    analyze.add_argument("--configuration")
    analyze.add_argument("--define", action="append", default=[])
    analyze.add_argument("--runtime-log", action="append", default=[])
    analyze.add_argument("--runtime-pattern")
    analyze.add_argument("--runtime-time-format")
    analyze.add_argument("--runtime-session", default="default")
    analyze.add_argument("--compare-runtime-log", action="append", default=[])
    analyze.add_argument("--compare-session", default="compare")
    analyze.add_argument("--diagnose-start")
    analyze.add_argument("--diagnose-end")
    analyze.add_argument("--no-cache", action="store_true")
    analyze.add_argument("--jobs", type=int, default=1, help="parallel clang translation units")
    analyze.add_argument("--static-store", help="SQLite path for out-of-core function metadata, or 'auto' for .cseq/static/symbols.db")
    analyze.add_argument("--summary-only", action="store_true", help="omit per-function rows from analyze output")
    analyze.set_defaults(handler=_analyze)

    html_cmd = sub.add_parser("html", help="generate HTML from an analyzed source tree")
    html_cmd.add_argument("path")
    html_cmd.add_argument("--entry", default="main")
    html_cmd.add_argument("--output", required=True)
    html_cmd.add_argument("--mode", choices=["portable","bundle","range","auto"], default="auto")
    html_cmd.add_argument("--bundle-threshold-events", type=int, default=10000)
    html_cmd.add_argument("--range-threshold-events", type=int, default=100000)
    html_cmd.add_argument("--viewport-size", type=int, default=200)
    html_cmd.add_argument("--config")
    html_cmd.add_argument("--compile-db")
    html_cmd.add_argument("--linker-map")
    html_cmd.add_argument("--binary")
    html_cmd.add_argument("--dwarf")
    html_cmd.add_argument("--configuration")
    html_cmd.add_argument("--define", action="append", default=[])
    html_cmd.add_argument("--runtime-log", action="append", default=[])
    html_cmd.add_argument("--runtime-pattern")
    html_cmd.add_argument("--runtime-time-format")
    html_cmd.add_argument("--runtime-session", default="default")
    html_cmd.add_argument("--compare-runtime-log", action="append", default=[])
    html_cmd.add_argument("--compare-session", default="compare")
    html_cmd.add_argument("--diagnose-start")
    html_cmd.add_argument("--diagnose-end")
    html_cmd.add_argument("--jobs", type=int, default=1, help="parallel clang translation units")
    html_cmd.add_argument("--static-store", help="SQLite path for out-of-core function metadata, or 'auto' for .cseq/static/symbols.db")
    html_cmd.set_defaults(handler=_html_command)

    puml_cmd = sub.add_parser("plantuml", help="generate PlantUML from an analyzed source tree")
    puml_cmd.add_argument("path")
    puml_cmd.add_argument("--entry", default="main")
    puml_cmd.add_argument("--output", required=True)
    puml_cmd.add_argument("--config")
    puml_cmd.add_argument("--compile-db")
    puml_cmd.add_argument("--linker-map")
    puml_cmd.add_argument("--binary")
    puml_cmd.add_argument("--dwarf")
    puml_cmd.add_argument("--configuration")
    puml_cmd.add_argument("--define", action="append", default=[])
    puml_cmd.add_argument("--jobs", type=int, default=1, help="parallel clang translation units")
    puml_cmd.add_argument("--static-store", help="SQLite path for out-of-core function metadata, or 'auto' for .cseq/static/symbols.db")
    puml_cmd.set_defaults(handler=_plantuml_command)

    cache_cmd = sub.add_parser("cache", help="inspect or clear parser cache")
    cache_sub = cache_cmd.add_subparsers(dest="cache_command", required=True)
    cache_info = cache_sub.add_parser("info")
    cache_info.add_argument("path")
    cache_info.set_defaults(handler=_cache_info)
    cache_clear = cache_sub.add_parser("clear")
    cache_clear.add_argument("path")
    cache_clear.set_defaults(handler=_cache_clear)

    config_cmd = sub.add_parser("config", help="initialize or validate cseq.toml")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_init = config_sub.add_parser("init", help="create a conservative cseq.toml")
    config_init.add_argument("path", nargs="?", default=".")
    config_init.add_argument("--force", action="store_true")
    config_init.set_defaults(handler=_config_init)
    config_validate = config_sub.add_parser("validate", help="validate cseq.toml")
    config_validate.add_argument("path", nargs="?", default="cseq.toml")
    config_validate.set_defaults(handler=_config_validate)

    marker = sub.add_parser("marker", help="manage runtime source markers")
    marker_sub = marker.add_subparsers(dest="marker_command", required=True)

    marker_apply = marker_sub.add_parser("apply", help="insert a marker into an existing string literal")
    marker_apply.add_argument("path")
    marker_apply.add_argument("--contains", required=True, dest="literal_contains")
    marker_apply.add_argument("--occurrence", type=int, default=0)
    marker_apply.add_argument("--id", dest="marker_id")
    marker_apply.add_argument("--expected-hash")
    marker_apply.add_argument("--project-root")
    marker_apply.add_argument("--manifest", required=True)
    marker_apply.add_argument(
        "--overlay-output",
        help="write the marker-injected source to this path and leave the original unchanged",
    )
    marker_apply.set_defaults(handler=_marker_apply)

    marker_undo = marker_sub.add_parser("undo", help="undo a marker rewrite using its manifest")
    marker_undo.add_argument("path")
    marker_undo.add_argument("--manifest", required=True)
    marker_undo.set_defaults(handler=_marker_undo)

    marker_audit = marker_sub.add_parser("audit", help="audit marker duplicates and malformed IDs")
    marker_audit.add_argument("path")
    marker_audit.set_defaults(handler=_marker_audit)

    trace = sub.add_parser("trace", help="import runtime marker logs")
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)
    trace_import = trace_sub.add_parser("import", help="parse one or more runtime logs")
    trace_import.add_argument("paths", nargs="+")
    trace_import.add_argument("--session", default="default")
    trace_import.add_argument("--pattern")
    trace_import.add_argument("--time-format")
    trace_import.add_argument("--binary")
    trace_import.add_argument("--dwarf")
    trace_import.add_argument("--load-bias", type=lambda x: int(x, 0), default=0)
    trace_import.add_argument("--address-key", action="append", default=[])
    trace_import.add_argument("--json", action="store_true", dest="as_json")
    trace_import.add_argument("--store", help="persist session to SQLite Event Store")
    trace_import.add_argument("--stream", action="store_true", help="stream logs directly into Event Store without materializing TraceSession")
    trace_import.add_argument("--chunk-size", type=int, default=10000)
    trace_import.add_argument("--batch-size", type=int, default=50000)
    trace_import.add_argument("--commit-every", type=int, default=250000)
    trace_import.set_defaults(handler=_trace_import)

    diff = sub.add_parser("diff", help="compare two runtime sessions")
    diff.add_argument("--left", action="append", required=True)
    diff.add_argument("--right", action="append", required=True)
    diff.add_argument("--left-session", default="left")
    diff.add_argument("--right-session", default="right")
    diff.add_argument("--pattern")
    diff.add_argument("--time-format")
    diff.add_argument("--json", action="store_true", dest="as_json")
    diff.add_argument("--html")
    diff.set_defaults(handler=_diff)

    explain = sub.add_parser("explain", help="explain why a call target was resolved")
    explain.add_argument("path")
    explain.add_argument("--caller", required=True)
    explain.add_argument("--callee")
    explain.add_argument("--config")
    explain.add_argument("--compile-db")
    explain.add_argument("--linker-map")
    explain.add_argument("--binary")
    explain.add_argument("--dwarf")
    explain.add_argument("--configuration")
    explain.add_argument("--define", action="append", default=[])
    explain.add_argument("--jobs", type=int, default=1, help="parallel clang translation units")
    explain.add_argument("--static-store", help="SQLite path for out-of-core function metadata, or 'auto' for .cseq/static/symbols.db")
    explain.set_defaults(handler=_explain)

    query = sub.add_parser("query", help="query calls, unresolved sites, or static paths")
    query.add_argument("path")
    query.add_argument("kind", choices=["calls", "unresolved", "paths"])
    query.add_argument("--function")
    query.add_argument("--start")
    query.add_argument("--end")
    query.add_argument("--config")
    query.add_argument("--compile-db")
    query.add_argument("--linker-map")
    query.add_argument("--binary")
    query.add_argument("--dwarf")
    query.add_argument("--configuration")
    query.add_argument("--define", action="append", default=[])
    query.add_argument("--jobs", type=int, default=1, help="parallel clang translation units")
    query.add_argument("--static-store", help="SQLite path for out-of-core function metadata, or 'auto' for .cseq/static/symbols.db")
    query.set_defaults(handler=_query)

    docs_cmd = sub.add_parser("docs", help="show or export bundled README, design document, and verification report")
    docs_sub = docs_cmd.add_subparsers(dest="docs_command", required=True)
    for kind, help_text in (
        ("readme", "show/export README"),
        ("design", "show/export the cleaned design document"),
        ("report", "show/export the HTML implementation and verification report"),
    ):
        d = docs_sub.add_parser(kind, help=help_text)
        d.add_argument("--output", help="write to this path instead of stdout")
        d.set_defaults(handler=_docs)
    docs_all = docs_sub.add_parser("all", help="export all bundled documents")
    docs_all.add_argument("--output-dir", default="cseq-docs", help="destination directory (default: cseq-docs)")
    docs_all.set_defaults(handler=_docs)

    serve = sub.add_parser("serve", help="serve generated HTML/bundle files over local HTTP")
    serve.add_argument("path", nargs="?", default=".")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)
    return p


def _docs(args: argparse.Namespace) -> int:
    if args.docs_command == "all":
        paths = export_all(args.output_dir)
        for path in paths:
            print(path)
        return 0
    path = export_document(args.docs_command, args.output)
    if path is not None:
        print(path)
    return 0


def _serve(args: argparse.Namespace) -> int:
    from pathlib import Path
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"cseq: not a directory: {root}", file=sys.stderr)
        return 3
    try:
        server = make_server(root, args.host, args.port)
    except OSError as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 3
    host, port = server.server_address[:2]
    print(f"cseq serving {root} at http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _doctor(args: argparse.Namespace) -> int:
    clang = shutil.which("clang")
    parser_env = probe_parser_environment()
    payload = {
        "cseq_version": __version__,
        "python": sys.version.split()[0],
        "clang": clang,
        "semantic_backend_ready": bool(clang),
        "canonical_fast_frontend": "tree-sitter-c",
        "parser_environment": parser_env.to_dict(),
        "g2b_resume": build_g2b_resume_report(getattr(args, "parser_wheel_dir", None)).to_dict(),
        "current_execution_mode": (
            "tree-sitter+clang"
            if parser_env.canonical_fast_frontend_ready and clang
            else "tree-sitter-syntax-only"
            if parser_env.canonical_fast_frontend_ready
            else "lexical-fallback+clang"
            if clang
            else "lexical-fallback-only"
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    # `doctor` is a diagnostic/reporting command. Component readiness is
    # represented explicitly in the JSON payload (semantic_backend_ready,
    # canonical_fast_frontend_ready, g2b_resume.status, current_execution_mode)
    # and must not be collapsed into an unrelated process exit code. A
    # successfully produced diagnostic report therefore exits 0 even when an
    # optional/currently-unavailable backend (for example clang) is absent.
    return 0



def _advanced_html_inputs(index, args, runtime_session):
    pattern = RuntimePattern(args.runtime_pattern, args.runtime_time_format) if getattr(args, "runtime_pattern", None) else None
    trace_diff = None
    compare_logs = getattr(args, "compare_runtime_log", []) or []
    if runtime_session is not None and compare_logs:
        other = build_trace_session(getattr(args, "compare_session", "compare"), compare_logs, pattern=pattern)
        trace_diff = diff_sessions(runtime_session, other)
    suggestions = []
    start = getattr(args, "diagnose_start", None)
    end = getattr(args, "diagnose_end", None)
    if start and end:
        reconstructed = reconstruct_static_paths(index, start, end)
        if reconstructed.state == ReconstructionState.AMBIGUOUS_PATH:
            suggestions = score_marker_suggestions(reconstructed.paths, count=5)
    return trace_diff, suggestions

def _write_html_output(
    output: str, model, *, runtime_session=None, cpu_evidence=(), explanations=None,
    trace_diff=None, marker_suggestions=None, mode: str = "auto",
    bundle_threshold_events: int = 10000, range_threshold_events: int = 100000, viewport_size: int = 200,
) -> dict[str, object]:
    from pathlib import Path
    event_count = len(runtime_session.events) if runtime_session else 0
    selected = mode
    if selected == "auto":
        selected = "range" if event_count > max(0, int(range_threshold_events)) else ("bundle" if event_count > max(0, int(bundle_threshold_events)) else "portable")
    kwargs = dict(
        runtime_session=runtime_session, cpu_evidence=cpu_evidence,
        explanations=explanations, trace_diff=trace_diff,
        marker_suggestions=marker_suggestions, viewport_size=viewport_size,
    )
    if selected == "bundle":
        html_path, data_path = write_html_bundle(output, model, **kwargs)
        return {"mode": "bundle", "html": str(html_path), "data": str(data_path), "events": event_count}
    if selected == "range":
        if runtime_session is None:
            Path(output).write_text(render_html(model, **kwargs), encoding="utf-8")
            return {"mode": "portable", "html": str(output), "data": None, "events": 0}
        html_path, data_path, index_path = write_html_range_bundle(output, model, **kwargs)
        return {"mode": "range", "html": str(html_path), "data": str(data_path), "index": str(index_path), "events": event_count}
    Path(output).write_text(render_html(model, **kwargs), encoding="utf-8")
    return {"mode": "portable", "html": str(output), "data": None, "events": event_count}


def _analyze(args: argparse.Namespace) -> int:
    try:
        project = Project(
            args.path,
            config=args.config,
            configuration=args.configuration,
            defines=tuple(args.define),
            use_cache=not args.no_cache,
            compile_db=args.compile_db,
            linker_map=args.linker_map,
            binary=args.binary,
            dwarf=args.dwarf,
            jobs=args.jobs,
            static_store=args.static_store,
        )
        index = project.index()
    except (OSError, RuntimeError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 3

    payload = {
        "root": str(index.root),
        "source_files": len(index.source_files),
        "translation_units": len(index.parse_artifacts),
        "cache": {
            "parser": {"hits": project.cache_hits, "misses": project.cache_misses},
            "analysis": {"hits": project.analysis_cache_hits, "misses": project.analysis_cache_misses},
            "source_index": {
                "hash_hits": project.source_index.hash_hits if project.source_index is not None else 0,
                "hash_misses": project.source_index.hash_misses if project.source_index is not None else 0,
                "dependency_hits": project.source_index.dependency_hits if project.source_index is not None else 0,
                "dependency_misses": project.source_index.dependency_misses if project.source_index is not None else 0,
                "path": str(project.source_index_path) if project.source_index is not None else None,
            },
            "enabled": not args.no_cache,
        },
        "parser_strategy": {
            "fast_translation_units": project.fast_artifacts,
            "semantic_translation_units": project.semantic_artifacts,
        },
        "static_store": str(project.static_symbol_store.path) if project.static_symbol_store is not None else None,
        "static_store_stats": {
            "hits": project.static_store_hits,
            "misses": project.static_store_misses,
            "removed_translation_units": project.static_store_removed_tus,
            "translation_units": project.static_symbol_store.translation_unit_count() if project.static_symbol_store is not None else 0,
            "artifact_sidecar_hits": project.static_artifact_hits,
            "artifact_sidecar_misses": project.static_artifact_misses,
        },
        "function_count": index.function_count(),
        "functions": [] if args.summary_only else [
            {
                "name": f.name,
                "id": f.qualified_id,
                "source": f.source_path,
                "line": f.source_range.start_line,
                "storage": f.storage_class,
            }
            for f in index.functions()
        ],
        "callsites": [
            {
                "caller": c.caller_name,
                "callee": c.callee_name,
                "raw": c.raw_text,
                "target_kind": c.target_kind.value,
                "target_function_ids": list(c.target_function_ids),
                "unknown_possible": c.unknown_possible,
                "callee_slot": c.callee_slot_key,
            }
            for c in index.callsites
        ],
        "cpu_evidence": [
            {
                "function_id": e.target_function_id,
                "layer": e.layer.value,
                "kind": e.value_kind.value,
                "value": e.value,
                "provenance": e.provenance,
                "explicit_override": e.explicit_override,
            }
            for e in index.cpu_evidence
        ],
        "linker_evidence": [
            {
                "symbol": e.symbol_name, "address": e.address,
                "object_file": e.object_file, "section": e.section,
                "matched_function_ids": list(e.matched_function_ids),
                "provenance": e.provenance,
            }
            for e in index.linker_evidence
        ],
        "binary_evidence": [
            {
                "symbol": e.symbol_name, "address": e.address, "size": e.size,
                "kind": e.symbol_kind, "matched_function_ids": list(e.matched_function_ids),
                "binary_hash": e.binary_hash, "provenance": e.provenance,
            } for e in index.binary_evidence
        ],
        "dwarf_evidence": [
            {
                "function_id": e.function_id, "source": e.source_path,
                "line": e.line, "address": e.address, "dwarf_file": e.dwarf_file,
                "provenance": e.provenance,
            } for e in index.dwarf_evidence
        ],
        "plugins": {
            "enabled": list(project.config.plugins_enabled),
            "loaded": [p.descriptor.name for p in project.plugins.plugins],
            "evidence": [
                {"plugin": e.plugin_name, "kind": e.kind.value, "target": e.target, "type": e.evidence_type, "value": e.value, "confidence": e.confidence}
                for e in project.plugin_result.evidence
            ],
            "diagnostics": [
                {"plugin": d.plugin_name, "severity": d.severity, "code": d.code, "message": d.message}
                for d in project.plugin_result.diagnostics
            ],
        },
        "artifacts": [
            {
                "source": a.translation_unit.source.project_relative_path,
                "status": a.status.value,
                "backend": a.parser_backend,
                "diagnostics": [
                    {"severity": d.severity, "message": d.message} for d in a.diagnostics
                ],
            }
            for a in index.parse_artifacts
        ],
    }
    model = None
    if args.plantuml or args.html:
        model = build_sequence(index, args.entry)
    if args.plantuml:
        from pathlib import Path
        Path(args.plantuml).write_text(render_plantuml(model), encoding="utf-8")
    if args.html:
        from pathlib import Path
        runtime_session = None
        if args.runtime_log:
            runtime_pattern = RuntimePattern(args.runtime_pattern, args.runtime_time_format) if args.runtime_pattern else None
            runtime_session = build_trace_session(args.runtime_session, args.runtime_log, pattern=runtime_pattern)
            apply_runtime_cpu_evidence(index, runtime_session)
            payload["cpu_evidence"] = [
                {
                    "function_id": e.target_function_id,
                    "layer": e.layer.value,
                    "kind": e.value_kind.value,
                    "value": e.value,
                    "provenance": e.provenance,
                    "explicit_override": e.explicit_override,
                }
                for e in index.cpu_evidence
            ]
        explanation_map = {}
        for caller in sorted({c.caller_name for c in index.callsites}):
            explanation_map[caller] = explain_call(index, caller=caller).get("calls", [])
        trace_diff, suggestions = _advanced_html_inputs(index, args, runtime_session)
        html_result = _write_html_output(
            args.html, model, runtime_session=runtime_session, cpu_evidence=index.cpu_evidence,
            explanations=explanation_map, trace_diff=trace_diff, marker_suggestions=suggestions,
            mode=args.html_mode, bundle_threshold_events=args.bundle_threshold_events,
            range_threshold_events=args.range_threshold_events, viewport_size=args.viewport_size,
        )
        payload["html_output"] = html_result
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"root: {payload['root']}")
        print(f"source files: {payload['source_files']}")
        print(f"translation units: {payload['translation_units']}")
        print(f"functions: {len(payload['functions'])}")
        for f in payload["functions"]:
            print(f"  {f['source']}:{f['line'] or '?'} {f['name']}")
        for a in payload["artifacts"]:
            if a["status"] != "OK":
                print(f"  [{a['status']}] {a['source']}")
    return 0



def _project_for_output(args: argparse.Namespace):
    project = Project(
        args.path, config=args.config, configuration=args.configuration,
        defines=tuple(args.define), compile_db=getattr(args, "compile_db", None),
        linker_map=getattr(args, "linker_map", None),
        binary=getattr(args, "binary", None),
        dwarf=getattr(args, "dwarf", None),
        jobs=getattr(args, "jobs", 1),
        static_store=getattr(args, "static_store", None),
    )
    return project, project.index()


def _html_command(args: argparse.Namespace) -> int:
    from pathlib import Path
    try:
        project, index = _project_for_output(args)
        model = build_sequence(index, args.entry)
        session = None
        if args.runtime_log:
            pattern = RuntimePattern(args.runtime_pattern, args.runtime_time_format) if args.runtime_pattern else None
            session = build_trace_session(args.runtime_session, args.runtime_log, pattern=pattern)
            apply_runtime_cpu_evidence(index, session)
        explanation_map = {
            caller: explain_call(index, caller=caller).get("calls", [])
            for caller in sorted({c.caller_name for c in index.callsites})
        }
        trace_diff, suggestions = _advanced_html_inputs(index, args, session)
        html_result = _write_html_output(
            args.output, model, runtime_session=session, cpu_evidence=index.cpu_evidence,
            explanations=explanation_map, trace_diff=trace_diff, marker_suggestions=suggestions,
            mode=args.mode, bundle_threshold_events=args.bundle_threshold_events,
            range_threshold_events=args.range_threshold_events, viewport_size=args.viewport_size,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr); return 3
    print(json.dumps({"output": args.output, "html": html_result, "cache_hits": project.cache_hits, "cache_misses": project.cache_misses, "analysis_cache_hits": project.analysis_cache_hits, "analysis_cache_misses": project.analysis_cache_misses, "fast_translation_units": project.fast_artifacts, "semantic_translation_units": project.semantic_artifacts, "source_index": {"hash_hits": project.source_index.hash_hits if project.source_index is not None else 0, "hash_misses": project.source_index.hash_misses if project.source_index is not None else 0, "dependency_hits": project.source_index.dependency_hits if project.source_index is not None else 0, "dependency_misses": project.source_index.dependency_misses if project.source_index is not None else 0}}, ensure_ascii=False))
    return 0


def _plantuml_command(args: argparse.Namespace) -> int:
    from pathlib import Path
    try:
        project, index = _project_for_output(args)
        model = build_sequence(index, args.entry)
        Path(args.output).write_text(render_plantuml(model), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr); return 3
    print(json.dumps({"output": args.output, "cache_hits": project.cache_hits, "cache_misses": project.cache_misses, "analysis_cache_hits": project.analysis_cache_hits, "analysis_cache_misses": project.analysis_cache_misses, "fast_translation_units": project.fast_artifacts, "semantic_translation_units": project.semantic_artifacts, "source_index": {"hash_hits": project.source_index.hash_hits if project.source_index is not None else 0, "hash_misses": project.source_index.hash_misses if project.source_index is not None else 0, "dependency_hits": project.source_index.dependency_hits if project.source_index is not None else 0, "dependency_misses": project.source_index.dependency_misses if project.source_index is not None else 0}}, ensure_ascii=False))
    return 0


def _cache_root(path: str) -> "Path":
    from pathlib import Path
    return Path(path).resolve() / ".cseq" / "cache"


def _cache_info(args: argparse.Namespace) -> int:
    from pathlib import Path
    root = _cache_root(args.path)
    out = {}
    for name in ("parser", "analysis", "static-artifact"):
        part = root / name
        files = list(part.rglob("*.json")) if part.exists() else []
        out[name] = {"root": str(part), "entries": len(files), "size_bytes": sum(x.stat().st_size for x in files)}
    source_db = Path(args.path).resolve() / ".cseq" / "source" / "index.db"
    out["source-index"] = {
        "root": str(source_db),
        "entries": 1 if source_db.exists() else 0,
        "size_bytes": source_db.stat().st_size if source_db.exists() else 0,
    }
    # Preserve the historical parser/analysis cache totals; Source Index is a
    # separate persistent metadata accelerator and is reported explicitly.
    out["entries"] = sum(out[name]["entries"] for name in ("parser", "analysis", "static-artifact"))
    out["size_bytes"] = sum(out[name]["size_bytes"] for name in ("parser", "analysis", "static-artifact"))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cache_clear(args: argparse.Namespace) -> int:
    import shutil as _shutil
    from pathlib import Path
    root = _cache_root(args.path)
    entries = len(list(root.rglob("*.json"))) if root.exists() else 0
    if root.exists():
        _shutil.rmtree(root)
    source_root = Path(args.path).resolve() / ".cseq" / "source"
    source_removed = int((source_root / "index.db").exists())
    if source_root.exists():
        _shutil.rmtree(source_root)
    print(json.dumps({"root": str(root), "removed_entries": entries, "source_index_removed": source_removed}, ensure_ascii=False))
    return 0


def _config_init(args: argparse.Namespace) -> int:
    from pathlib import Path
    target = Path(args.path)
    if target.is_dir() or target.suffix == "":
        target = target / "cseq.toml"
    target = target.resolve()
    if target.exists() and not args.force:
        print(f"cseq: config already exists: {target}", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    compile_present = (target.parent / "compile_commands.json").is_file()
    target.write_text(default_config_text(compile_commands_present=compile_present), encoding="utf-8")
    print(json.dumps({"path": str(target), "compile_commands_detected": compile_present}, ensure_ascii=False))
    return 0


def _config_validate(args: argparse.Namespace) -> int:
    try:
        cfg = CseqConfig.load(args.path)
        issues = validate_config(cfg)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "issues": [{"severity": "error", "code": "LOAD_ERROR", "message": str(exc)}]}, ensure_ascii=False, indent=2))
        return 2
    errors = [x for x in issues if x["severity"] == "error"]
    print(json.dumps({"valid": not errors, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def _marker_apply(args: argparse.Namespace) -> int:
    try:
        if args.overlay_output:
            manifest = apply_marker_overlay(
                args.path, args.overlay_output,
                literal_contains=args.literal_contains,
                occurrence=args.occurrence,
                expected_hash=args.expected_hash,
                project_root=args.project_root,
                marker_id=args.marker_id,
                manifest_path=args.manifest,
            )
        else:
            manifest = apply_marker(
                args.path,
                literal_contains=args.literal_contains,
                occurrence=args.occurrence,
                expected_hash=args.expected_hash,
                project_root=args.project_root,
                marker_id=args.marker_id,
                manifest_path=args.manifest,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 6
    print(manifest.to_json())
    return 0


def _marker_undo(args: argparse.Namespace) -> int:
    try:
        undo_marker(args.path, args.manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 6
    return 0


def _marker_audit(args: argparse.Namespace) -> int:
    from pathlib import Path
    root = Path(args.path)
    if root.is_dir():
        paths = [x.path for x in scan_sources(root)]
    else:
        paths = [root]
    result = audit_markers(paths)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 5 if result["duplicates"] or result["malformed"] else 0



def _diff(args: argparse.Namespace) -> int:
    try:
        pattern = RuntimePattern(args.pattern, args.time_format) if args.pattern else None
        left = build_trace_session(args.left_session, args.left, pattern=pattern)
        right = build_trace_session(args.right_session, args.right, pattern=pattern)
        result = diff_sessions(left, right)
    except (OSError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr); return 3
    payload = {
        "left_session": result.left_session,
        "right_session": result.right_session,
        "added_markers": list(result.added_markers),
        "missing_markers": list(result.missing_markers),
        "changed_cpu": list(result.changed_cpu),
        "changed_task": list(result.changed_task),
    }
    if args.html:
        from pathlib import Path
        from .sequence import SequenceModel
        Path(args.html).write_text(render_html(SequenceModel("trace-diff", []), trace_diff=result), encoding="utf-8")
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{result.left_session} -> {result.right_session}")
        print(f"added markers: {len(result.added_markers)}")
        print(f"missing markers: {len(result.missing_markers)}")
        print(f"CPU changes: {len(result.changed_cpu)}")
        print(f"Task changes: {len(result.changed_task)}")
    return 0

def _trace_import(args: argparse.Namespace) -> int:
    pattern = RuntimePattern(args.pattern, args.time_format) if args.pattern else None
    if args.stream and (args.binary or args.dwarf or args.address_key):
        print("cseq: runtime address evidence currently requires non-stream trace import", file=sys.stderr)
        return 2
    if args.stream:
        if not args.store:
            print("cseq: trace import --stream requires --store", file=sys.stderr)
            return 2
        try:
            with EventStore(args.store) as store:
                count = store.replace_session_events_chunked(
                    args.session,
                    iter_runtime_files(args.paths, pattern=pattern),
                    chunk_size=args.chunk_size,
                    batch_size=args.batch_size,
                    commit_every=args.commit_every,
                    rebuild_indexes=True,
                )
                store_summary = store.summary(args.session)
                status = store.session_status(args.session)
        except (OSError, ValueError) as exc:
            print(f"cseq: {exc}", file=sys.stderr)
            return 3
        payload = {
            "session": args.session,
            "status": status,
            "streamed": True,
            "event_store": store_summary,
            "source_logs": [str(x) for x in args.paths],
            "event_count": count,
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"session: {args.session}")
            print(f"events: {count}")
            print(f"status: {status}")
        return 0

    try:
        session = build_trace_session(args.session, args.paths, pattern=pattern)
    except (OSError, ValueError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 3
    address_evidence = []
    if args.binary:
        try:
            image = load_binary_image(args.binary)
            dwarf = load_dwarf_lines(args.dwarf) if args.dwarf else None
            address_evidence = discover_runtime_address_evidence(
                session.events, image, dwarf=dwarf, load_bias=args.load_bias, trusted_keys=args.address_key,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"cseq: {exc}", file=sys.stderr)
            return 3
    elif args.dwarf:
        print("cseq: --dwarf requires --binary for runtime address resolution", file=sys.stderr)
        return 2

    if args.store:
        with EventStore(args.store) as store:
            store.replace_session(session)
            store_summary = store.summary(session.name)
    else:
        store_summary = None
    payload = {
        "session": session.name,
        "event_store": store_summary,
        "source_logs": session.source_logs,
        "runtime_address_evidence": [
            {
                "event_index": e.event_index, "marker_id": e.marker_id,
                "key": e.payload_key, "value": e.payload_value,
                "runtime_address": e.runtime_address, "linked_address": e.linked_address,
                "symbol": e.symbol_name, "symbol_size": e.symbol_size,
                "source_file": e.source_file, "source_line": e.source_line,
                "confidence": e.confidence, "provenance": e.provenance,
            } for e in address_evidence
        ],
        "events": [
            {
                "event_index": e.event_index,
                "marker_id": e.marker_id,
                "timestamp": e.timestamp_raw,
                "cpu": e.cpu_hint,
                "task": e.task_hint,
                "payload": e.payload_dict(),
                "raw": e.raw_line,
            }
            for e in session.events
        ],
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"session: {session.name}")
        print(f"events: {len(session.events)}")
        for e in session.events:
            print(f"  {e.event_index} {e.cpu_hint or '?'} {e.marker_id} {e.message or ''}")
    return 0


def _explain(args: argparse.Namespace) -> int:
    try:
        index = Project(
            args.path,
            config=args.config,
            configuration=args.configuration,
            defines=tuple(args.define),
            compile_db=getattr(args, "compile_db", None),
            linker_map=getattr(args, "linker_map", None),
            binary=getattr(args, "binary", None),
            dwarf=getattr(args, "dwarf", None),
            jobs=getattr(args, "jobs", 1),
        ).index()
    except (OSError, RuntimeError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 3
    payload = explain_call(index, caller=args.caller, callee_text=args.callee)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["found"] else 5


def _query(args: argparse.Namespace) -> int:
    try:
        index = Project(
            args.path,
            config=args.config,
            configuration=args.configuration,
            defines=tuple(args.define),
            compile_db=getattr(args, "compile_db", None),
            linker_map=getattr(args, "linker_map", None),
            binary=getattr(args, "binary", None),
            dwarf=getattr(args, "dwarf", None),
            jobs=getattr(args, "jobs", 1),
        ).index()
    except (OSError, RuntimeError) as exc:
        print(f"cseq: {exc}", file=sys.stderr)
        return 3
    if args.kind == "calls":
        if not args.function:
            print("cseq: query calls requires --function", file=sys.stderr); return 2
        payload = query_calls(index, args.function)
    elif args.kind == "unresolved":
        payload = query_unresolved(index)
    else:
        if not args.start or not args.end:
            print("cseq: query paths requires --start and --end", file=sys.stderr); return 2
        payload = query_paths(index, args.start, args.end)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
