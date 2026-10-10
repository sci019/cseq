from __future__ import annotations

import dataclasses
import sys

_ORIGINAL_DATACLASS = dataclasses.dataclass

def _compat_dataclass(*args, **kwargs):
    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
    return _ORIGINAL_DATACLASS(*args, **kwargs)

def install_dataclass_compat() -> None:
    """Patch only Python 3.9, where stdlib dataclass lacks slots=."""
    if sys.version_info < (3, 10) and dataclasses.dataclass is not _compat_dataclass:
        dataclasses.dataclass = _compat_dataclass
