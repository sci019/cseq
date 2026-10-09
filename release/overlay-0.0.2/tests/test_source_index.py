from pathlib import Path

from cseq.dependencies import include_dependency_fingerprint
from cseq.model import TranslationUnit
from cseq.project import Project
from cseq.scanner import scan_sources
from cseq.source_index import SourceFingerprintIndex


def test_source_hash_reuses_stat_identity_and_invalidates_on_edit(tmp_path: Path):
    src = tmp_path / "main.c"
    src.write_text("int main(void){return 0;}\n", encoding="utf-8")
    idx = SourceFingerprintIndex(tmp_path / "index.db")

    first = idx.content_hash(src)
    assert idx.hash_misses == 1
    second = idx.content_hash(src)
    assert second == first
    assert idx.hash_hits == 1

    src.write_text("int main(void){return 1;}\n", encoding="utf-8")
    third = idx.content_hash(src)
    assert third != first
    assert idx.hash_misses == 2


def test_dependency_snapshot_reuses_without_rescanning_unchanged_tu(tmp_path: Path):
    header = tmp_path / "dep.h"
    header.write_text("#define V 1\n", encoding="utf-8")
    src = tmp_path / "main.c"
    src.write_text('#include "dep.h"\nint main(void){return V;}\n', encoding="utf-8")
    idx = SourceFingerprintIndex(tmp_path / "index.db")
    source = next(x for x in scan_sources(tmp_path, fingerprint_index=idx) if x.suffix == ".c")
    tu = TranslationUnit(source=source, working_directory=tmp_path)

    first = include_dependency_fingerprint(tu, idx)
    assert idx.dependency_hits == 0
    second = include_dependency_fingerprint(tu, idx)
    assert second == first
    assert idx.dependency_hits == 1

    header.write_text("#define V 2\n", encoding="utf-8")
    third = include_dependency_fingerprint(tu, idx)
    assert third != first
    assert idx.dependency_misses >= 2


def test_project_warm_static_store_reuses_source_and_dependency_index(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "dep.h").write_text("#define V 1\n", encoding="utf-8")
    (root / "main.c").write_text('#include "dep.h"\nint helper(void){return V;}\nint main(void){return helper();}\n', encoding="utf-8")
    store = root / ".cseq" / "static" / "symbols.db"

    cold = Project(root, use_cache=False, static_store=store)
    cold_index = cold.index()
    assert cold_index.function_count() == 2
    assert cold.source_index is not None
    assert cold.static_store_misses == 1

    warm = Project(root, use_cache=False, static_store=store)
    warm_index = warm.index()
    assert warm_index.function_count() == 2
    assert warm.static_store_hits == 1
    assert warm.static_artifact_hits == 1
    assert warm.source_index is not None
    assert warm.source_index.dependency_hits == 1
    # main.c + dep.h are both unchanged and should reuse persistent SHA values.
    assert warm.source_index.hash_hits >= 2


def test_cache_clear_reports_and_removes_source_index(tmp_path: Path, capsys):
    from cseq.cli import main
    import json
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    Project(root).index()
    source_db = root / ".cseq" / "source" / "index.db"
    assert source_db.is_file()
    assert main(["cache", "info", str(root)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["source-index"]["entries"] == 1
    assert main(["cache", "clear", str(root)]) == 0
    cleared = json.loads(capsys.readouterr().out)
    assert cleared["source_index_removed"] == 1
    assert not source_db.exists()


def test_source_index_accepts_filesystem_ids_above_sqlite_int64(tmp_path, monkeypatch):
    from cseq.source_index import SourceFingerprintIndex

    source = tmp_path / "huge-id.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    index = SourceFingerprintIndex(tmp_path / "index.db")
    real_identity = index._stat_identity(source)
    huge_identity = (
        real_identity[0],
        real_identity[1],
        real_identity[2],
        str(2**80 + 123),
        str(2**96 + 456),
    )
    monkeypatch.setattr(index, "_stat_identity", lambda path: huge_identity)

    first = index.content_hash(source)
    index.commit()
    second = index.content_hash(source)

    assert first == second
    row = index.connection.execute("SELECT dev,ino FROM files WHERE path=?", (str(source.resolve()),)).fetchone()
    assert row == (huge_identity[3], huge_identity[4])
    index.close()
