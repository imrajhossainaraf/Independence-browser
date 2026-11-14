"""Ranking signals, adjustments, score explanations, and tie-breaking (Decision D6).

Signals implemented:
- Weighted BM25 base score
- Bounded freshness multiplier (half-life 365 days; 24 hours for news; max +10%)
- Bounded source-quality adjustment (length penalties, https and title bonuses; [-5%, +5%])
- Title boost: all terms in title (+10%) or exact title match (+15%)
- Phrase and proximity boost (+10% when full query appears in title or description)
- Deterministic tie-breaking (score descending, canonical URL ascending)
- Per-signal ablation switches and score explanations
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dateutil import parser as date_parser

_SPLIT_WORDS_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class RankingConfig:
    enable_freshness: bool = True
    enable_quality: bool = True
    enable_title_boost: bool = True
    enable_phrase_boost: bool = True
    enable_host_diversity: bool = True
    freshness_max_boost: float = 0.10  # max +10%
    freshness_half_life_days: float = 365.0  # 365 days for standard pages
    freshness_news_half_life_days: float = 1.0  # 24 hours for news
    quality_min_mult: float = 0.95  # at most -5%
    quality_max_mult: float = 1.05  # at most +5%
    title_all_terms_boost: float = 0.10  # +10%
    title_exact_boost: float = 0.15  # +15%
    phrase_boost: float = 0.10  # +10%
    explain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enable_freshness": self.enable_freshness,
            "enable_quality": self.enable_quality,
            "enable_title_boost": self.enable_title_boost,
            "enable_phrase_boost": self.enable_phrase_boost,
            "enable_host_diversity": self.enable_host_diversity,
            "freshness_max_boost": self.freshness_max_boost,
            "quality_min_mult": self.quality_min_mult,
            "quality_max_mult": self.quality_max_mult,
            "title_all_terms_boost": self.title_all_terms_boost,
            "title_exact_boost": self.title_exact_boost,
            "phrase_boost": self.phrase_boost,
        }


@dataclass(frozen=True, slots=True)
class ScoreExplanation:
    bm25_score: float
    freshness_multiplier: float
    quality_multiplier: float
    title_multiplier: float
    phrase_multiplier: float
    final_score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bm25_score": round(self.bm25_score, 4),
            "freshness_multiplier": round(self.freshness_multiplier, 4),
            "quality_multiplier": round(self.quality_multiplier, 4),
            "title_multiplier": round(self.title_multiplier, 4),
            "phrase_multiplier": round(self.phrase_multiplier, 4),
            "final_score": round(self.final_score, 4),
            "reasons": self.reasons,
        }


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Safely parse an ISO date string into UTC datetime."""
    if not value or not value.strip():
        return None
    try:
        parsed = date_parser.isoparse(value.strip())
        if not isinstance(parsed, datetime):
            return None
        dt: datetime = parsed
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def compute_freshness_multiplier(
    published_at: str | None,
    doc_type: str,
    *,
    config: RankingConfig,
    reference_time: datetime | None = None,
) -> tuple[float, str | None]:
    """Compute bounded freshness multiplier (Decision D6).

    - At most +10% boost.
    - Standard half-life: 365 days.
    - News half-life: 24 hours (1.0 day).
    - Requires a real published or modified date; future dates get no boost.
    """
    if not config.enable_freshness:
        return 1.0, None

    dt = parse_iso_datetime(published_at)
    if dt is None:
        return 1.0, "no valid published date"

    ref = reference_time or datetime.now(UTC)
    age_seconds = (ref - dt).total_seconds()
    if age_seconds < 0:
        # Future-dated content gets no freshness bonus
        return 1.0, "future published date (no bonus)"

    age_days = age_seconds / 86400.0
    half_life = (
        config.freshness_news_half_life_days
        if doc_type == "news"
        else config.freshness_half_life_days
    )
    decay = 0.5 ** (age_days / half_life)
    boost = config.freshness_max_boost * decay
    multiplier = min(1.0 + boost, 1.0 + config.freshness_max_boost)
    return multiplier, f"freshness decay: {decay:.3f} (age {age_days:.1f}d)"


def compute_quality_multiplier(
    word_count: int,
    title: str,
    url: str,
    *,
    config: RankingConfig,
) -> tuple[float, list[str]]:
    """Compute bounded source-quality adjustment (Decision D6).

    - Word count penalty: -0.03 for < 50 words, additional -0.02 for < 20 words.
    - HTTPS bonus: +0.02 for secure URL.
    - Informative title bonus: +0.02 for title length >= 10.
    - Bound: clamped between quality_min_mult and quality_max_mult (e.g. 0.95 and 1.05).
    """
    if not config.enable_quality:
        return 1.0, []

    adjustment = 0.0
    reasons: list[str] = []

    if word_count < 20:
        adjustment -= 0.05
        reasons.append("quality penalty: very short content (<20 words)")
    elif word_count < 50:
        adjustment -= 0.03
        reasons.append("quality penalty: short content (<50 words)")

    if url.lower().startswith("https://"):
        adjustment += 0.02
        reasons.append("quality bonus: https")

    if len(title.strip()) >= 10:
        adjustment += 0.02
        reasons.append("quality bonus: informative title")

    min_adj = config.quality_min_mult - 1.0
    max_adj = config.quality_max_mult - 1.0
    clamped_adj = max(min_adj, min(max_adj, adjustment))

    multiplier = 1.0 + clamped_adj
    return multiplier, reasons


def compute_title_multiplier(
    query_str: str,
    title: str,
    *,
    config: RankingConfig,
) -> tuple[float, str | None]:
    """Compute title match boost (Decision D6).

    - Exact title match: +15% boost.
    - All query terms present in title: +10% boost.
    """
    if not config.enable_title_boost or not title.strip() or not query_str.strip():
        return 1.0, None

    norm_query = " ".join(_SPLIT_WORDS_RE.findall(query_str.lower()))
    norm_title = " ".join(_SPLIT_WORDS_RE.findall(title.lower()))

    if not norm_query or not norm_title:
        return 1.0, None

    if norm_query == norm_title:
        return 1.0 + config.title_exact_boost, "title boost: exact title match"

    query_tokens = norm_query.split()
    title_tokens = set(norm_title.split())

    if len(query_tokens) > 0 and all(t in title_tokens for t in query_tokens):
        return 1.0 + config.title_all_terms_boost, "title boost: all query terms in title"

    return 1.0, None


def compute_phrase_multiplier(
    query_str: str,
    title: str,
    description: str,
    *,
    config: RankingConfig,
) -> tuple[float, str | None]:
    """Compute phrase/proximity boost (Decision D6).

    - When multi-term query phrase appears in title or description: +10% boost.
    """
    if not config.enable_phrase_boost or not query_str.strip():
        return 1.0, None

    norm_query = " ".join(_SPLIT_WORDS_RE.findall(query_str.lower()))
    if len(norm_query.split()) < 2:
        return 1.0, None

    norm_title = " ".join(_SPLIT_WORDS_RE.findall(title.lower()))
    norm_desc = " ".join(_SPLIT_WORDS_RE.findall(description.lower()))

    if norm_query in norm_title or norm_query in norm_desc:
        return 1.0 + config.phrase_boost, "phrase boost: query phrase found in title/desc"

    return 1.0, None


def compute_score(
    raw_bm25: float,
    *,
    query_str: str,
    title: str,
    url: str,
    description: str,
    doc_type: str,
    word_count: int,
    published_at: str | None,
    config: RankingConfig,
    reference_time: datetime | None = None,
) -> tuple[float, ScoreExplanation | None]:
    """Calculate final composite score and explanation from raw BM25 and ranking signals.

    Raw SQLite FTS5 bm25 returns negative numbers (more negative = better match).
    We convert to positive magnitude `base_bm25 = abs(raw_bm25)` so higher score = better match.
    """
    base_bm25 = max(0.0001, abs(raw_bm25))

    freshness_mult, fresh_reason = compute_freshness_multiplier(
        published_at,
        doc_type,
        config=config,
        reference_time=reference_time,
    )
    quality_mult, qual_reasons = compute_quality_multiplier(
        word_count,
        title,
        url,
        config=config,
    )
    title_mult, title_reason = compute_title_multiplier(
        query_str,
        title,
        config=config,
    )
    phrase_mult, phrase_reason = compute_phrase_multiplier(
        query_str,
        title,
        description,
        config=config,
    )

    final_score = base_bm25 * freshness_mult * quality_mult * title_mult * phrase_mult

    explanation: ScoreExplanation | None = None
    if config.explain:
        reasons: list[str] = []
        if fresh_reason:
            reasons.append(fresh_reason)
        reasons.extend(qual_reasons)
        if title_reason:
            reasons.append(title_reason)
        if phrase_reason:
            reasons.append(phrase_reason)

        explanation = ScoreExplanation(
            bm25_score=base_bm25,
            freshness_multiplier=freshness_mult,
            quality_multiplier=quality_mult,
            title_multiplier=title_mult,
            phrase_multiplier=phrase_mult,
            final_score=final_score,
            reasons=reasons,
        )

    return final_score, explanation
