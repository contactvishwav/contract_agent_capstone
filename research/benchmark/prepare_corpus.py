"""
One-time (offline-cacheable) prep step for the clause-extraction benchmark.

extraction_benchmark.csv has real CUAD ground truth (Document Name, Parties,
Agreement/Effective/Expiration Date) keyed by contract Filename, but no
contract full text - so there's nothing to actually run extraction against.
This script sources that full text from the same underlying dataset
(theatticusproject/cuad-qa on HuggingFace, SQuAD-style with one row per
question but a full `context` field per contract) and writes a local JSON
cache keyed by filename, so evaluate_extraction.py can run fully offline
afterward without re-downloading anything or requiring internet access.

Requires the `datasets` package (not part of backend's runtime dependencies -
this is a one-off data-prep script, not application code):
    pip install "datasets>=2.20.0,<3.0.0"

Usage:
    python research/benchmark/prepare_corpus.py
"""

import csv
import json
import os

BENCHMARK_CSV = os.path.join(os.path.dirname(__file__), "extraction_benchmark.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "cuad_contract_texts.json")


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
    # extraction_benchmark.csv's Filename column has a ".pdf" suffix; the
    # dataset's `title` field does not.
    needed_titles = {fn[:-4] if fn.lower().endswith(".pdf") else fn for fn in needed}

    corpus = {}
    for split in ("train", "test"):
        print(f"Loading theatticusproject/cuad-qa ({split} split) ...")
        ds = load_dataset("theatticusproject/cuad-qa", split=split, trust_remote_code=True)
        for row in ds:
            title = row["title"]
            if title in needed_titles and title not in corpus:
                corpus[title] = row["context"]

    missing = needed_titles - corpus.keys()
    print(f"Matched {len(corpus)}/{len(needed_titles)} contracts from the benchmark CSV.")
    if missing:
        print(f"WARNING: {len(missing)} filenames from extraction_benchmark.csv had no match in the dataset:")
        for m in sorted(missing)[:10]:
            print(f"  - {m}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(corpus, f)

    print(f"Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes)")


if __name__ == "__main__":
    main()
