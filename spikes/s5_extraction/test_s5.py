"""Unit test for Spike S5 extraction bakeoff."""

from pathlib import Path

from spikes.s5_extraction.bakeoff import evaluate_fixtures

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_spike_s5_extraction_bakeoff() -> None:
    results = evaluate_fixtures(FIXTURES_DIR)
    assert len(results) == 20

    comb_scores = [r.combined_f1 for r in results]
    avg_f1 = sum(comb_scores) / len(comb_scores)
    min_f1 = min(comb_scores)
    leakage = any(r.leakage_detected for r in results)

    assert avg_f1 >= 0.90
    assert min_f1 >= 0.80
    assert not leakage
