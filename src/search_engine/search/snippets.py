"""Safe query-biased snippet extraction and text highlighting (Step 14).

Security Invariant:
No HTML string ever leaves the server. Snippets and titles are returned as
lists of structured HighlightSegment instances (text pieces flagged as matched or not).
Any HTML or malicious payloads (e.g. <script>, onload=) in the source document
remain literal text in HighlightSegment.text and are never evaluated as markup.

Features:
- Sentence splitting utility handling abbreviations, decimals, lists, and CJK punctuation
- Best-passage selection scored by distinct matched terms, density, proximity, and position
- Up to two passage windows joined by an ellipsis segment
- Unicode-aware, diacritic-insensitive term matching
- Fallback snippet (description or first sentences) when query terms do not appear in body
- Clear metadata-only label for documents with no stored body text
- Bounded snippet length with word-boundary awareness
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_WORD_BOUNDARY_RE = re.compile(r"\b\w+\b", re.UNICODE)

# Common abbreviations that do NOT terminate sentences
_COMMON_ABBREVS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "vs",
    "eg",
    "ie",
    "etc",
    "al",
    "fig",
    "no",
    "vol",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
    "u.s",
    "u.k",
    "e.u",
    "p.m",
    "a.m",
    "pm",
    "am",
    "approx",
}


@dataclass(frozen=True, slots=True)
class HighlightSegment:
    text: str
    is_match: bool

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "is_match": self.is_match}


def split_sentences(text: str) -> list[str]:
    """Split text into sentences handling abbreviations, decimals, and CJK terminators.

    Reusable across snippet extraction and Step 21 answer synopsis.
    """
    if not text or not text.strip():
        return []

    cleaned = text.strip()
    sentences: list[str] = []
    current: list[str] = []
    i = 0
    n = len(cleaned)

    while i < n:
        ch = cleaned[i]
        current.append(ch)

        # CJK sentence boundaries: 。！？
        if ch in ("。", "！", "？"):
            sent = "".join(current).strip()
            if sent:
                sentences.append(sent)
            current = []
            i += 1
            continue

        # Standard boundaries: . ? !
        if ch in (".", "?", "!"):
            # Check if followed by whitespace or end of text
            is_end = i + 1 == n
            is_followed_by_space = not is_end and cleaned[i + 1] in (" ", "\n", "\r", "\t")

            if is_end or is_followed_by_space:
                # Check for decimal numbers (e.g. 3.14 or v0.1.0)
                is_decimal = False
                if ch == "." and i > 0 and i + 1 < n:
                    if cleaned[i - 1].isdigit() and cleaned[i + 1].isdigit():
                        is_decimal = True

                # Check for known abbreviation (e.g. Dr., e.g., vs.)
                is_abbrev = False
                if ch == "." and not is_decimal:
                    # Look ahead: if next non-whitespace char is lowercase, it's not a sentence end
                    k = i + 1
                    while k < n and cleaned[k] in (" ", "\n", "\r", "\t"):
                        k += 1
                    if k < n and cleaned[k].islower():
                        is_abbrev = True

                    if not is_abbrev:
                        # Look backwards to find word token before dot
                        token_chars: list[str] = []
                        j = i - 1
                        while j >= 0 and (cleaned[j].isalnum() or cleaned[j] == "."):
                            token_chars.append(cleaned[j])
                            j -= 1
                        token = "".join(reversed(token_chars)).lower()
                        token_clean = token.replace(".", "")
                        if (
                            token in _COMMON_ABBREVS
                            or token_clean in _COMMON_ABBREVS
                            or len(token_clean) == 1
                        ):
                            is_abbrev = True

                if not is_decimal and not is_abbrev:
                    sent = "".join(current).strip()
                    if sent:
                        sentences.append(sent)
                    current = []
                    # Skip trailing whitespace
                    while i + 1 < n and cleaned[i + 1] in (" ", "\n", "\r", "\t"):
                        i += 1

        i += 1

    remaining = "".join(current).strip()
    if remaining:
        sentences.append(remaining)

    return sentences


def strip_accents(text: str) -> str:
    """Remove diacritical marks from characters using NFD decomposition."""
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _normalize_token_for_match(token: str) -> str:
    """Normalize token by casefolding and stripping diacritics."""
    return strip_accents(unicodedata.normalize("NFKC", token).casefold())


def highlight_text(text: str, query_terms: list[str]) -> list[HighlightSegment]:
    """Split text into HighlightSegment instances without modifying characters.

    Matches query_terms case-insensitively and diacritic-insensitively,
    preserving exact original characters in the output.
    """
    if not text:
        return []

    norm_terms = {_normalize_token_for_match(t) for t in query_terms if t.strip()}
    if not norm_terms:
        return [HighlightSegment(text=text, is_match=False)]

    # Find character spans for each word match
    matches: list[tuple[int, int]] = []
    for m in _WORD_BOUNDARY_RE.finditer(text):
        token_str = m.group(0)
        norm_token = _normalize_token_for_match(token_str)
        if norm_token in norm_terms or any(t in norm_token and len(t) >= 4 for t in norm_terms):
            matches.append((m.start(), m.end()))

    if not matches:
        return [HighlightSegment(text=text, is_match=False)]

    # Merge overlapping or contiguous match spans
    merged_spans: list[tuple[int, int]] = []
    for start, end in sorted(matches):
        if not merged_spans:
            merged_spans.append((start, end))
        else:
            last_start, last_end = merged_spans[-1]
            if start <= last_end:
                merged_spans[-1] = (last_start, max(last_end, end))
            else:
                merged_spans.append((start, end))

    # Slice text into matched and non-matched segments
    segments: list[HighlightSegment] = []
    cursor = 0
    for m_start, m_end in merged_spans:
        if m_start > cursor:
            segments.append(HighlightSegment(text=text[cursor:m_start], is_match=False))
        segments.append(HighlightSegment(text=text[m_start:m_end], is_match=True))
        cursor = m_end

    if cursor < len(text):
        segments.append(HighlightSegment(text=text[cursor:], is_match=False))

    return segments


def _score_passage(
    passage: str,
    query_terms: set[str],
    position_index: int,
) -> float:
    """Score a candidate sentence passage by distinct matched terms, density, and position."""
    norm_passage = _normalize_token_for_match(passage)
    tokens = _WORD_BOUNDARY_RE.findall(norm_passage)
    if not tokens:
        return 0.0

    matched_distinct: set[str] = set()
    total_matches = 0
    first_match_idx = -1
    last_match_idx = -1

    for idx, t in enumerate(tokens):
        if t in query_terms or any(q in t and len(q) >= 4 for q in query_terms):
            matched_distinct.add(t)
            total_matches += 1
            if first_match_idx == -1:
                first_match_idx = idx
            last_match_idx = idx

    if not matched_distinct:
        return 0.0

    # 1. Distinct matched terms: primary signal
    score = len(matched_distinct) * 100.0

    # 2. Total matches: frequency signal
    score += total_matches * 10.0

    # 3. Proximity / span compactness: more compact span gives higher score
    if last_match_idx >= first_match_idx >= 0:
        span_len = last_match_idx - first_match_idx + 1
        proximity = len(matched_distinct) / span_len
        score += proximity * 25.0

    # 4. Position bias: earlier passages slightly favored
    position_decay = 1.0 / (1.0 + (0.1 * position_index))
    score *= position_decay

    return score


def extract_snippet(
    body_text: str | None,
    query: str,
    *,
    title: str = "",
    description: str = "",
    is_metadata_only: bool = False,
    max_chars: int = 180,
) -> list[HighlightSegment]:
    """Extract query-biased best-passage snippet returning structured HighlightSegments.

    - If document is metadata-only or body is empty: uses description or returns metadata label.
    - Scores candidate sentences by distinct matches, density, and proximity.
    - Joins up to two strong windows with an ellipsis segment.
    - Boundaries respected (never cuts mid-word).
    """
    query_tokens = _WORD_BOUNDARY_RE.findall(query)
    norm_query_terms = {_normalize_token_for_match(t) for t in query_tokens if t.strip()}

    # Case A: Document marked metadata_only or empty body
    if is_metadata_only or not body_text or not body_text.strip():
        if description.strip():
            desc = description.strip()
            if len(desc) > max_chars:
                desc = desc[:max_chars].rsplit(" ", 1)[0] + "..."
            return highlight_text(desc, query_tokens)
        return [HighlightSegment(text="Metadata only. No body text available.", is_match=False)]

    cleaned_body = body_text.strip()
    sentences = split_sentences(cleaned_body)
    if not sentences:
        sentences = [cleaned_body]

    # Score each sentence as a candidate window
    scored_passages: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences):
        score = _score_passage(sent, norm_query_terms, idx)
        if score > 0.0:
            scored_passages.append((score, idx, sent))

    # Case B: No sentences matched query terms -> fallback to description or first sentence
    if not scored_passages:
        fallback_text = description.strip() if description.strip() else sentences[0]
        if len(fallback_text) > max_chars:
            fallback_text = fallback_text[:max_chars].rsplit(" ", 1)[0] + "..."
        return highlight_text(fallback_text, query_tokens)

    # Sort candidates by score descending
    scored_passages.sort(key=lambda x: -x[0])
    best_score, best_idx, best_sent = scored_passages[0]

    # Trim best passage if it exceeds max_chars
    if len(best_sent) > max_chars:
        # Find position of first match
        matches = list(_WORD_BOUNDARY_RE.finditer(best_sent))
        first_match_start = 0
        for m in matches:
            if _normalize_token_for_match(m.group(0)) in norm_query_terms:
                first_match_start = m.start()
                break

        # Center window around first match
        start_char = max(0, first_match_start - 40)
        end_char = min(len(best_sent), start_char + max_chars)

        # Snap to word boundaries
        if start_char > 0:
            space_pos = best_sent.find(" ", start_char)
            if space_pos != -1 and space_pos < start_char + 20:
                start_char = space_pos + 1
        if end_char < len(best_sent):
            space_pos = best_sent.rfind(" ", start_char, end_char)
            if space_pos != -1 and space_pos > end_char - 20:
                end_char = space_pos

        snippet_str = best_sent[start_char:end_char].strip()
        if start_char > 0:
            snippet_str = "..." + snippet_str
        if end_char < len(best_sent):
            snippet_str = snippet_str + "..."
        return highlight_text(snippet_str, query_tokens)

    # Check if a second strong window can fit within max_chars
    selected: list[tuple[int, str]] = [(best_idx, best_sent)]
    current_len = len(best_sent)

    for _next_score, next_idx, next_sent in scored_passages[1:]:
        if abs(next_idx - best_idx) <= 1:
            # Skip directly adjacent sentences to favor diverse context
            continue
        extra_len = len(next_sent) + 5  # include ' ... ' separator
        if current_len + extra_len <= max_chars:
            selected.append((next_idx, next_sent))
            current_len += extra_len
            break

    # Order selected windows by document position
    selected.sort(key=lambda x: x[0])
    joined_snippets: list[HighlightSegment] = []

    for i, (_pos, passage_text) in enumerate(selected):
        if i > 0:
            joined_snippets.append(HighlightSegment(text=" ... ", is_match=False))
        joined_snippets.extend(highlight_text(passage_text, query_tokens))

    return joined_snippets
