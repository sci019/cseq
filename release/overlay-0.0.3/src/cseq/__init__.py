"""cseq: C source sequence analysis tool."""

from ._compat import install_dataclass_compat
install_dataclass_compat()

__version__ = "0.0.3"

from .api import Analysis, Explanation, MarkerSuggestion, QueryResult, RuntimeSession, analyze

__all__ = [
    "Analysis", "Explanation", "MarkerSuggestion", "QueryResult", "RuntimeSession", "analyze", "__version__"
]
