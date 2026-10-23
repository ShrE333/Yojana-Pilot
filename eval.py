"""Print the accuracy numbers for the judges: python eval.py"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from core import check_eligibility, compare_names, rank_schemes  # noqa: E402
from core.loader import load_scheme_dir  # noqa: E402

TESTS = Path(__file__).parent / "tests"
# The personas were hand-labelled when only these three schemes existed, so the start-with
# check runs against them. Ranking over the whole database is reported separately, because a
# better scheme found later is an improvement, not a regression.
CURATED = {k: {**v, "origin": "curated", "verified": True}
           for k, v in load_scheme_dir(TESTS / "fixtures" / "schemes").items()}


def eligibility_eval():
    personas = yaml.safe_load((TESTS / "personas.yaml").read_text(encoding="utf-8"))
    total = correct = first_ok = 0
    for p in personas:
        results = check_eligibility(p["profile"], CURATED)
        got = {r.scheme_id: r.status for r in results}
        for sid, exp in p["expected"].items():
            total += 1
            correct += got.get(sid) == exp
        start = rank_schemes(results, CURATED).start_with
        first_ok += (start.scheme_id if start else None) == p["start_with"]
    print(f"Eligibility: {correct}/{total} scheme decisions correct ({100*correct/total:.1f}%) "
          f"across {len(personas)} personas")
    print(f"Start-with pick: {first_ok}/{len(personas)} correct (on the {len(CURATED)} labelled schemes)")
    catalog_eval(personas)


def catalog_eval(personas) -> None:
    """What the personas get from the whole database. No labels exist for this yet, so it reports
    reach rather than accuracy: how many schemes each person is offered, and how many are obscure."""
    from core.awareness import awareness
    from discovery.catalog import catalog_for
    from saathi.shortlist import build_shortlist
    offered = lesser = 0
    for p in personas:
        cat = catalog_for(p["profile"])
        sl = build_shortlist(p["profile"], cat)
        offered += (1 if sl["start_with"] else 0) + len(sl["others"])
        lesser += len(sl["lesser_known"])
    n = len(personas)
    print(f"Whole database ({len(catalog_for({}))} schemes): {offered/n:.1f} schemes offered per persona, "
          f"{lesser/n:.1f} of them lesser-known")


def matcher_eval():
    pairs = yaml.safe_load((TESTS / "name_pairs.yaml").read_text(encoding="utf-8"))
    # positive class = "needs fixing" (probable_mismatch or mismatch)
    tp = fp = fn = tn = 0
    for a, b, label in pairs:
        pred = compare_names(a, b).verdict
        actual_pos, pred_pos = label != "match", pred != "match"
        tp += actual_pos and pred_pos
        fp += pred_pos and not actual_pos
        fn += actual_pos and not pred_pos
        tn += not actual_pos and not pred_pos
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    print(f"Name mismatch detection on {len(pairs)} pairs: precision {precision:.2f}, recall {recall:.2f}")


if __name__ == "__main__":
    eligibility_eval()
    matcher_eval()
