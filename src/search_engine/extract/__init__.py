"""Content extraction and adapters."""

from search_engine.extract.html import extract_html
from search_engine.extract.models import (
    ExtractedDocument,
    MediaRef,
    OutgoingLink,
    RobotsMeta,
)

__all__ = [
    "ExtractedDocument",
    "MediaRef",
    "OutgoingLink",
    "RobotsMeta",
    "extract_html",
]
