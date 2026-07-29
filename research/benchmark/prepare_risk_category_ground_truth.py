"""
One-time (offline-cacheable) prep step extending the clause-extraction
benchmark to the ~36 risk-relevant CUAD categories that extraction_
benchmark.csv doesn't cover (it only has Document Name, Parties, and the
three date fields - metadata, not the categories a risk assessment
actually depends on: Cap On Liability, Non-Compete, Termination For
Convenience, Ip Ownership Assignment, etc.).

Sources ground truth for the OTHER 36 categories from the same underlying
dataset prepare_corpus.py already uses (theatticusproject/cuad-qa on
HuggingFace) - it has one row per (contract, category) question with an
`answers` field (list of verbatim text spans, empty if that category
doesn't apply to that contract). Restricted to the same set of contracts
already in extraction_benchmark.csv, so the existing corpus/sampling
infrastructure in evaluate_extraction.py keeps working unchanged.

Requires the `datasets` package (see prepare_corpus.py).

Usage:
    python research/benchmark/prepare_risk_category_ground_truth.py
"""

import csv
import json
import os
import re
from collections import defaultdict

BENCHMARK_CSV = os.path.join(os.path.dirname(__file__), "extraction_benchmark.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "risk_category_ground_truth.json")

# The 5 categories already covered by extraction_benchmark.csv - everything
# else in the dataset's 41 categories is a "risk category" for this purpose.
ALREADY_COVERED = {"Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date"}


def load_benchmark_filenames() -> set:
    filenames = set()
    with open(BENCHMARK_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filenames.add(row["Filename"])
    return filenames


def main():
    from datasets import load_dataset

    needed = load_benchmark_filenames()
    needed_titles = {fn[:-4] if fn.lower().endswith(".pdf") else fn for fn in needed}

    # {title: {category: [text_span, ...]}}
    ground_truth = defaultdict(dict)
    category_pattern = re.compile(r'related to "([^"]+)"')
    seen_categories = set()

    for split in ("train", "test"):
        print(f"Loading theatticusproject/cuad-qa ({split} split) ...")
        ds = load_dataset("theatticusproject/cuad-qa", split=split, trust_remote_code=True)
        for row in ds:
            title = row["title"]
            if title not in needed_titles:
                continue
            match = category_pattern.search(row["question"])
            if not match:
                continue
            category = match.group(1)
            seen_categories.add(category)
            if category in ALREADY_COVERED:
                continue
            ground_truth[title][category] = list(row["answers"]["text"])

    risk_categories = seen_categories - ALREADY_COVERED
    print(f"Found {len(risk_categories)} risk-relevant categories across {len(ground_truth)} contracts.")

    missing = needed_titles - ground_truth.keys()
    if missing:
        print(f"WARNING: {len(missing)} filenames from extraction_benchmark.csv had no matching rows:")
        for m in sorted(missing)[:10]:
            print(f"  - {m}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f)

    print(f"Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes)")


if __name__ == "__main__":
    main()
