from __future__ import annotations
try:
    from importlib import resources
    if not hasattr(resources, "files"):
        raise ImportError
except ImportError:  # Python 3.8
    import importlib_resources as resources
from pathlib import Path
import sys
_DOCS={"readme":("README.md","README.md"),"design":("design.md","シーケンス解析_設計書.md"),"report":("verification_report.html","実装・検証成績書.html")}
def document_bytes(kind:str)->bytes:
    try: resource_name,_=_DOCS[kind]
    except KeyError as exc: raise ValueError(f"unknown document kind: {kind}") from exc
    return resources.files("cseq").joinpath("resources",resource_name).read_bytes()
def export_document(kind:str,output:str|Path|None=None)->Path|None:
    data=document_bytes(kind)
    if output is None:
        if hasattr(sys.stdout,"buffer"): sys.stdout.buffer.write(data)
        else: sys.stdout.write(data.decode("utf-8"))
        return None
    out=Path(output); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(data); return out
def export_all(output_dir:str|Path)->list[Path]:
    root=Path(output_dir); root.mkdir(parents=True,exist_ok=True); out=[]
    for kind,(_,filename) in _DOCS.items():
        p=root/filename; export_document(kind,p); out.append(p)
    return out
