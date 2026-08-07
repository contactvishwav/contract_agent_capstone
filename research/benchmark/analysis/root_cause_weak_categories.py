"""
Root-cause analysis for the 20 risk-relevant CUAD categories scoring below
0.30 F1 (docs/EVALUATION.md §4b), using the ALREADY-COLLECTED per-contract
predictions in extraction_eval_checkpoint_risk-categories-flash-lite-497.jsonl
- no new Gemini calls needed for this pass, mirroring how the Expiration
Date root-cause finding was done from stored predictions, not a fresh run.

For each weak category, buckets every FN into:
  - "empty" - the model predicted nothing at all for this type (pure recall miss)
  - "unmatched" - the model predicted something, but text_match() didn't
    accept it as equivalent to any gold value (candidate matcher bug OR a
    genuinely wrong/hallucinated prediction - printed for manual read)
and prints a sample of "unmatched" and FP cases per category for manual
classification.
"""
import json
import sys
from collections import defaultdict

CHECKPOINT = "research/benchmark/extraction_eval_checkpoint_risk-categories-flash-lite-497.jsonl"

WEAK_CATEGORIES = [
    "Non-Disparagement", "Change Of Control", "Exclusivity", "Third Party Beneficiary",
    "Most Favored Nation", "Minimum Commitment", "Source Code Escrow", "Uncapped Liability",
    "Rofr/Rofo/Rofn", "Ip Ownership Assignment", "Revenue/Profit Sharing", "Joint Ip Ownership",
    "Affiliate License-Licensee", "Covenant Not To Sue", "Post-Termination Services",
    "Competitive Restriction Exception", "Price Restrictions", "Volume Restriction",
    "Affiliate License-Licensor", "Unlimited/All-You-Can-Eat-License",
]


def load_entries():
    entries = []
    with open(CHECKPOINT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def main():
    entries = load_entries()
    print(f"Loaded {len(entries)} contract entries\n")

    for category in WEAK_CATEGORIES:
        fn_empty = []
        fn_unmatched = []
        fp = []
        tp = 0

        for entry in entries:
            r = entry.get("results", {}).get(category)
            if not r:
                continue
            outcome = r["outcome"]
            if outcome == "TP":
                tp += 1
            elif outcome == "FN":
                if r["predicted"]:
                    fn_unmatched.append((entry["filename"], r["gold"], r["predicted"]))
                else:
                    fn_empty.append(entry["filename"])
            elif outcome == "FP":
                fp.append((entry["filename"], r["predicted"]))

        total_fn = len(fn_empty) + len(fn_unmatched)
        print("=" * 100)
        print(f"{category}  |  TP={tp} FN={total_fn} (empty={len(fn_empty)}, unmatched={len(fn_unmatched)}) FP={len(fp)}")
        print("=" * 100)

        if fn_unmatched:
            print(f"\n  -- {len(fn_unmatched)} FN-with-nonempty-prediction (matcher-artifact candidates) --")
            for fname, gold, pred in fn_unmatched[:5]:
                print(f"  [{fname[:60]}]")
                print(f"    GOLD:      {gold}")
                print(f"    PREDICTED: {pred}")
                print()

        if fp:
            print(f"\n  -- {len(fp)} FP examples (predicted but no gold) --")
            for fname, pred in fp[:3]:
                print(f"  [{fname[:60]}]")
                print(f"    PREDICTED: {pred}")
                print()

        print()


if __name__ == "__main__":
    main()
