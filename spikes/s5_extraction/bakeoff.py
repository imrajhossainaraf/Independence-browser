"""Spike S5: Text extraction bake-off between trafilatura, selectolax, and combined pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import trafilatura
from selectolax.lexbor import LexborHTMLParser

_WORDS = re.compile(r"\b\w+\b")
_REMOVE_SELECTOR = (
    "script, style, noscript, nav, header, footer, aside, form, svg, iframe, "
    "[role='dialog'], [id*='cookie'], [class*='cookie'], [id*='banner'], "
    "[class*='banner'], [class*='advertisement'], [id*='advertisement']"
)


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def token_f1(extracted: str, gold: str) -> tuple[float, float, float]:
    ext_tokens = tokenize(extracted)
    gold_tokens = tokenize(gold)
    if not ext_tokens or not gold_tokens:
        return (0.0, 0.0, 0.0)

    ext_set = set(ext_tokens)
    gold_set = set(gold_tokens)

    intersection = ext_set & gold_set
    if not intersection:
        return (0.0, 0.0, 0.0)

    precision = len(intersection) / len(ext_set)
    recall = len(intersection) / len(gold_set)
    f1 = 2 * precision * recall / (precision + recall)
    return (precision, recall, f1)


def extract_selectolax(html: str) -> str:
    tree = LexborHTMLParser(html)
    for tag in tree.css(_REMOVE_SELECTOR):
        tag.decompose()

    target = (
        tree.css_first("article")
        or tree.css_first("main")
        or tree.css_first("[role='main']")
        or tree.body
    )
    if target is None:
        return ""

    text = target.text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def extract_trafilatura(html: str) -> str:
    res = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        fast=True,
        favor_recall=True,
    )
    return res or ""


def extract_combined(html: str) -> str:
    text = extract_trafilatura(html)
    if len(tokenize(text)) < 40:
        fallback = extract_selectolax(html)
        if len(tokenize(fallback)) > len(tokenize(text)):
            return fallback
    return text


@dataclass
class PageResult:
    name: str
    trafilatura_f1: float
    selectolax_f1: float
    combined_f1: float
    leakage_detected: bool


def evaluate_fixtures(fixtures_dir: Path) -> list[PageResult]:
    results: list[PageResult] = []
    html_files = sorted(fixtures_dir.glob("*.html"))
    boilerplate_indicators = [
        "cookie policy",
        "all rights reserved",
        "advertisement",
        "sign in to your account",
    ]

    for html_file in html_files:
        gold_file = html_file.with_suffix(".gold.txt")
        if not gold_file.exists():
            continue
        html = html_file.read_text(encoding="utf-8")
        gold = gold_file.read_text(encoding="utf-8")

        ext_traf = extract_trafilatura(html)
        ext_sel = extract_selectolax(html)
        ext_comb = extract_combined(html)

        f1_traf = token_f1(ext_traf, gold)[2]
        f1_sel = token_f1(ext_sel, gold)[2]
        f1_comb = token_f1(ext_comb, gold)[2]

        leakage = any(ind in ext_comb.lower() for ind in boilerplate_indicators)
        results.append(
            PageResult(
                name=html_file.stem,
                trafilatura_f1=f1_traf,
                selectolax_f1=f1_sel,
                combined_f1=f1_comb,
                leakage_detected=leakage,
            )
        )
    return results
