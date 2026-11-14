"""Run Spike S5 text extraction bake-off across 20 fixtures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from spikes.s5_extraction.bakeoff import evaluate_fixtures

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def main() -> None:
    results = evaluate_fixtures(FIXTURES_DIR)
    assert len(results) == 20, f"Expected 20 fixtures, found {len(results)}"

    print(f"{'Page':<35} {'Trafilatura':<12} {'Selectolax':<12} {'Combined':<12} {'Leakage'}")
    print("-" * 80)

    traf_scores = []
    sel_scores = []
    comb_scores = []
    leakage_count = 0

    for r in results:
        traf_scores.append(r.trafilatura_f1)
        sel_scores.append(r.selectolax_f1)
        comb_scores.append(r.combined_f1)
        if r.leakage_detected:
            leakage_count += 1
        line = (
            f"{r.name:<30} "
            f"{r.trafilatura_f1:<10.3f} "
            f"{r.selectolax_f1:<10.3f} "
            f"{r.combined_f1:<10.3f} "
            f"{r.leakage_detected}"
        )
        print(line)

    avg_traf = sum(traf_scores) / len(traf_scores)
    avg_sel = sum(sel_scores) / len(sel_scores)
    avg_comb = sum(comb_scores) / len(comb_scores)
    min_comb = min(comb_scores)

    print("-" * 80)
    print(f"Avg F1 - Traf: {avg_traf:.3f}, Sel: {avg_sel:.3f}, Comb: {avg_comb:.3f}")
    print(f"Combined Min F1: {min_comb:.3f}, Boilerplate Leakage Count: {leakage_count}")

    # Check criteria: average >= 0.90, min >= 0.80, no boilerplate leakage
    assert avg_comb >= 0.90, f"Average F1 {avg_comb:.3f} < 0.90"
    assert min_comb >= 0.80, f"Minimum F1 {min_comb:.3f} < 0.80"
    assert leakage_count == 0, f"Leakage detected on {leakage_count} pages"
    print("\nSUCCESS: All Spike S5 criteria passed!")


if __name__ == "__main__":
    main()
