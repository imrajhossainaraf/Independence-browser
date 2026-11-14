"""Data models for extracted documents and page metadata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OutgoingLink:
    """An outgoing link discovered in a document."""

    url: str
    nofollow: bool
    anchor_text: str = ""


@dataclass(frozen=True)
class RobotsMeta:
    """Robots metadata directives extracted from HTML meta tags or headers."""

    noindex: bool = False
    nofollow: bool = False
    noarchive: bool = False
    nosnippet: bool = False


@dataclass(frozen=True)
class MediaRef:
    """A reference to an embedded media element (image, video, audio, track)."""

    media_type: str  # "image", "video", "audio", "transcript"
    url: str | None
    alt: str = ""
    caption: str = ""
    title: str = ""


@dataclass
class ExtractedDocument:
    """The canonical representation of content extracted from a document."""

    final_url: str
    canonical_url: str | None
    title: str
    text: str
    headings: list[str] = field(default_factory=list)
    description: str = ""
    site_name: str = ""
    lang: str = "und"
    published_at: str | None = None
    outgoing_links: list[OutgoingLink] = field(default_factory=list)
    robots_meta: RobotsMeta = field(default_factory=RobotsMeta)
    content_hash: str = ""
    media_refs: list[MediaRef] = field(default_factory=list)
    image_candidates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0

    @property
    def headings_text(self) -> str:
        """All headings joined by space for FTS indexing."""
        return " ".join(self.headings)

    @property
    def is_indexable(self) -> bool:
        """True if robots meta directives permit indexing."""
        return not self.robots_meta.noindex
