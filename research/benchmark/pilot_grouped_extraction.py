"""
Phase 1 pilot: does splitting single-pass 41-category clause extraction into
LLMExtractionService.extract_clauses_grouped()'s 7 focused, parallel calls
(one prompt per CATEGORY_GROUPS group) meaningfully improve accuracy over
today's production approach (extract_clauses(): one 41-category call plus a
conditional fallback call for 8 near-zero-engagement categories)?

Motivated directly by the v3 weak-category pass (docs/EVALUATION.md §4c):
8 categories scored near-zero, hypothesized as attention dilution from
cramming 41 competing categories into one prompt. The fallback pass fixed
several of those 8 with a second, narrower call - this pilot tests whether
full, permanent isolation (every category, not just the 8) does better,
worse, or about the same, and at what real cost.

Methodology mirrors evaluate_extraction.py deliberately (same matching
logic, same gold data, same scoring function - imported, not reimplemented)
so the two runs' numbers are directly comparable:

  - The SINGLE-PASS baseline is NOT re-run. It's reused verbatim from
    extraction_eval_checkpoint_risk-categories-flash-lite-497-v3.jsonl -
    the real, already-measured v3 (prompt hints + fallback pass) production
    predictions for these exact contracts. Zero new LLM calls for this half
    of the comparison, matching this project's established
    "diagnose from data already in hand before spending new quota"
    discipline (docs/EVALUATION.md §4a/§4c).
  - The GROUPED approach is run for real, for the first time, only on a
    small stratified sample (this script's own selection - weighted toward
    contracts with real gold-positive examples in the 20 categories the v3
    pass targeted, since a random sample would likely contain zero examples
    of several rare categories and make their F1 uncomputable).

Same model (gemini-flash-lite-latest) as the v3 baseline it's compared
against - a model mismatch would confound "did grouping help" with "is this
just a better model," the same discipline this project already applied
when interpreting flash vs. flash-lite results (docs/EVALUATION.md §3/§5).

Usage:
    GOOGLE_API_KEY=... python research/benchmark/pilot_grouped_extraction.py \
        --sample-size 35 --rpm-limit 10
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import threading
import time
from collections import defaultdict, deque
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO_ROOT, "backend", ".env"))

from backend.agents.llm_extraction_service import (  # noqa: E402
    LLMExtractionService, CUADClauseType, CATEGORY_GROUPS, get_default_llm,
)
from evaluate_extraction import (  # noqa: E402
    evaluate_contract, parse_ground_truth_list, load_benchmark_rows,
    COLUMN_TO_TYPE, RISK_CATEGORY_TYPES,
    CORPUS_JSON, RISK_CATEGORIES_JSON, BENCHMARK_DIR,
)

CHECKPOINT_PATH = os.path.join(BENCHMARK_DIR, "extraction_eval_checkpoint_risk-categories-flash-lite-497-v3.jsonl")

# The 20 categories the v3 pass targeted (docs/EVALUATION.md §4c) - Group A
# (precision), Group B (= FALLBACK_CATEGORIES, near-zero engagement), Group
# C (recall gap). This pilot's sample is stratified toward these, since
# they're the ones the "attention dilution" hypothesis is actually about -
# a random sample would under-represent several of them badly (Source Code
# Escrow has only 2 gold-positive examples in the entire 243-contract
# checkpoint).
GROUP_A = ["Third Party Beneficiary", "Change Of Control"]
GROUP_B = [
    "Volume Restriction", "Competitive Restriction Exception", "Unlimited/All-You-Can-Eat-License",
    "Joint Ip Ownership", "Affiliate License-Licensee", "Affiliate License-Licensor",
    "Covenant Not To Sue", "Post-Termination Services",
]
GROUP_C = [
    "Non-Disparagement", "Most Favored Nation", "Source Code Escrow", "Rofr/Rofo/Rofn",
    "Ip Ownership Assignment", "Revenue/Profit Sharing", "Minimum Commitment",
    "Exclusivity", "Uncapped Liability", "Price Restrictions",
]
TARGET_CATEGORIES = GROUP_A + GROUP_B + GROUP_C
# A few already-strong categories included as a regression control - if
# grouping is going to hurt anything, it would plausibly be a category that
# already worked fine under the full 41-category prompt.
CONTROL_CATEGORIES = ["Document Name", "Parties", "Governing Law", "Anti-Assignment"]


class _ThreadSafeRpmLimiter:
    """
    Same sliding-window algorithm as evaluate_extraction.py's _RpmLimiter,
    with a lock added: that class is only ever called from one thread there.
    This pilot calls .wait() from up to 7 concurrent group-extraction
    threads per contract (extract_clauses_grouped's ThreadPoolExecutor), so
    the check-then-append needs to be atomic across threads or the pacing
    window can be silently violated by a race.
    """

    def __init__(self, rpm: Optional[int]):
        self.rpm = rpm
        self._times = deque()
        self._lock = threading.Lock()

    def wait(self):
        if not self.rpm:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] > 60.0:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                sleep_for = 60.0 - (now - self._times[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)


class _TokenBudgetLimiter:
    """
    Real, live-discovered constraint (found running this exact pilot): the
    free tier's actual binding limit here is NOT request count but a
    per-minute INPUT TOKEN budget (observed live: 250,000 input
    tokens/minute, quotaId GenerateContentInputTokensPerModelPerMinute-
    FreeTier). Request-count pacing alone (a plain requests/minute limiter,
    as evaluate_extraction.py's _RpmLimiter uses for single-pass extraction)
    is the wrong axis for grouped extraction specifically: every one of the
    7 group calls re-sends the FULL contract text (only the category list
    in the prompt shrinks per group, not the contract itself), so grouping
    multiplies real input-token volume per contract by ~7x versus
    single-pass's 1 call (or ~2x when its fallback pass fires) - the first
    concrete, measured cost consequence of this pilot's own design, found
    by hitting the real 429 mid-run rather than assumed in advance.

    Estimates tokens via len(text) // CHARS_PER_TOKEN_ESTIMATE (a
    conservative ~4 chars/token approximation - real tokenization varies,
    but this only needs to keep the estimate on the safe side of the real
    cap, not be exact) since getting an exact pre-call token count would
    need a second API call per estimate, defeating the point of pacing.
    """
    CHARS_PER_TOKEN_ESTIMATE = 4

    def __init__(self, tokens_per_minute: Optional[int]):
        self.budget = tokens_per_minute
        self._entries = deque()  # (timestamp, token_estimate)
        self._lock = threading.Lock()

    def estimate_tokens(self, prompt: str) -> int:
        return len(prompt) // self.CHARS_PER_TOKEN_ESTIMATE

    def wait(self, prompt: str):
        if not self.budget:
            return
        tokens = self.estimate_tokens(prompt)
        while True:
            with self._lock:
                now = time.monotonic()
                while self._entries and now - self._entries[0][0] > 60.0:
                    self._entries.popleft()
                used = sum(t for _, t in self._entries)
                if used + tokens <= self.budget:
                    self._entries.append((now, tokens))
                    return
                oldest_time = self._entries[0][0] if self._entries else now
                sleep_for = 60.0 - (now - oldest_time) + 0.5
            if sleep_for > 0:
                time.sleep(sleep_for)


def load_checkpoint_entries():
    entries = {}
    with open(CHECKPOINT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                entries[e["filename"]] = e
    return entries


def select_stratified_sample(checkpoint, risk_gt, sample_size, min_per_category=4):
    """
    Greedy coverage selection: repeatedly pick whichever remaining contract
    gains the most "still under-covered target category" credit, until every
    target category has min_per_category gold-positive examples (or the
    corpus genuinely doesn't have that many - e.g. Source Code Escrow) or
    sample_size is reached. Deterministic given the same checkpoint/risk_gt
    (dict iteration order is insertion order in Python 3.7+, and both source
    files are static), not seeded-random - this is a coverage-maximization
    problem, not a representativeness sample, so determinism here is a
    feature: reproducible without needing to persist a seed.
    """
    filenames = list(checkpoint.keys())

    def title_of(fn):
        return fn[:-4] if fn.lower().endswith(".pdf") else fn

    covers = {}
    for fn in filenames:
        title = title_of(fn)
        gold = risk_gt.get(title, {})
        covers[fn] = {cat for cat in TARGET_CATEGORIES if gold.get(cat)}

    remaining_need = {cat: min_per_category for cat in TARGET_CATEGORIES}
    selected = []
    candidates = set(filenames)

    while candidates and len(selected) < sample_size and any(v > 0 for v in remaining_need.values()):
        def score(fn):
            return sum(1 for cat in covers[fn] if remaining_need.get(cat, 0) > 0)

        best = max(candidates, key=score)
        if score(best) == 0:
            break
        selected.append(best)
        candidates.discard(best)
        for cat in covers[best]:
            if remaining_need.get(cat, 0) > 0:
                remaining_need[cat] -= 1

    # Fill any remaining budget with a few more contracts at random (fixed
    # order, not true randomness) so the sample size is actually hit even
    # after coverage needs are satisfied - gives more statistical weight to
    # already-covered categories rather than wasting budget.
    for fn in filenames:
        if len(selected) >= sample_size:
            break
        if fn not in selected:
            selected.append(fn)

    return selected[:sample_size], remaining_need


def _is_rate_limited(exc: Exception) -> bool:
    return "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc)


async def run_grouped_on_sample(sample_filenames, corpus, model_name, token_budget, rpm_limit):
    llm = get_default_llm(model_name)
    if llm is None:
        print("ERROR: no GOOGLE_API_KEY configured.")
        sys.exit(1)
    service = LLMExtractionService(llm)

    rpm_limiter = _ThreadSafeRpmLimiter(rpm_limit)
    token_limiter = _TokenBudgetLimiter(token_budget)
    original_invoke = service._invoke

    def paced_invoke(prompt, source_text, raise_on_error, _attempt=0):
        rpm_limiter.wait()
        token_limiter.wait(prompt)
        try:
            return original_invoke(prompt, source_text, raise_on_error=True)
        except Exception as e:
            if _is_rate_limited(e) and _attempt < 3:
                # A real 429 despite our own pacing (our token estimate is
                # approximate, and other traffic on the same key/project can
                # also consume the same shared quota) - back off and retry
                # rather than recording this group as "found nothing", which
                # would corrupt this pilot's own recall measurement with a
                # spurious failure indistinguishable from a genuine miss.
                wait_s = 20.0 * (_attempt + 1)
                print(f"    rate-limited, backing off {wait_s:.0f}s (attempt {_attempt + 1}/3): {e}")
                time.sleep(wait_s)
                return paced_invoke(prompt, source_text, raise_on_error, _attempt=_attempt + 1)
            if raise_on_error:
                raise
            return []

    service._invoke = paced_invoke

    # Incremental checkpoint (JSONL, append-only) - this run costs real
    # quota over many minutes; a crash/interrupt partway through (this pilot
    # was itself interrupted once already, mid-run, by the token-budget
    # finding above) should resume from what's already been paid for, not
    # restart from zero, matching evaluate_extraction.py's own
    # checkpoint-resume convention.
    ckpt_path = os.path.join(BENCHMARK_DIR, "pilot_grouped_extraction_checkpoint.jsonl")
    results = {}
    if os.path.exists(ckpt_path):
        with open(ckpt_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    results[entry["filename"]] = entry
        if results:
            print(f"Resuming from checkpoint: {len(results)} contracts already done ({ckpt_path}).")

    for i, fn in enumerate(sample_filenames, 1):
        if fn in results:
            print(f"[{i}/{len(sample_filenames)}] {fn}: already in checkpoint, skipping")
            continue

        title = fn[:-4] if fn.lower().endswith(".pdf") else fn
        text = corpus.get(title)
        if not text:
            print(f"[{i}/{len(sample_filenames)}] SKIP (no cached text): {fn}")
            continue

        t0 = time.monotonic()
        clauses, failures = await service.extract_clauses_grouped(text, raise_on_error=False)
        elapsed = time.monotonic() - t0

        entry = {
            "filename": fn,
            "extracted": [
                {"clause_type": c.clause_type.value, "extracted_text": c.extracted_text}
                for c in clauses
            ],
            "group_failures": failures,
            "elapsed_seconds": round(elapsed, 2),
            "call_count": len(CATEGORY_GROUPS),  # all 7 groups always fire, failures included
        }
        results[fn] = entry
        with open(ckpt_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        status = f"FAILURES: {list(failures.keys())}" if failures else "ok"
        print(f"[{i}/{len(sample_filenames)}] {fn}: {len(clauses)} clauses, "
              f"{elapsed:.1f}s, 7 groups ({status})")

    return results


def aggregate(entries_by_filename, checkpoint, corpus, risk_gt, benchmark_rows_by_title):
    """
    entries_by_filename: {filename: {"extracted": [...]}} - either the
    single-pass baseline (from checkpoint) or the grouped pilot's fresh
    results, same shape either way so this one function scores both.
    Returns {category: {"tp":, "fp":, "fn":}} aggregated across the sample.
    """
    counts = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    all_types = {**COLUMN_TO_TYPE, **RISK_CATEGORY_TYPES}

    for fn, entry in entries_by_filename.items():
        title = fn[:-4] if fn.lower().endswith(".pdf") else fn
        row = benchmark_rows_by_title.get(title)
        column_gold = (
            {column: parse_ground_truth_list(row.get(column, "")) for column in COLUMN_TO_TYPE}
            if row else {column: [] for column in COLUMN_TO_TYPE}
        )
        predicted_by_type = defaultdict(list)
        for e in entry["extracted"]:
            predicted_by_type[CUADClauseType(e["clause_type"])].append(e["extracted_text"])

        scored = evaluate_contract(column_gold, predicted_by_type, risk_gt.get(title, {}))
        for category, r in scored.items():
            clause_type = all_types.get(category)
            if clause_type is None:
                continue
            outcome = r["outcome"]
            if outcome == "TP":
                counts[category]["tp"] += 1
            elif outcome == "FP":
                counts[category]["fp"] += 1
            elif outcome == "FN":
                counts[category]["fn"] += 1

    return counts


def prf1(c):
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-size", type=int, default=35)
    parser.add_argument("--min-per-category", type=int, default=4,
                         help="Coverage target per target category during sample selection.")
    parser.add_argument("--rpm-limit", type=int, default=10)
    parser.add_argument("--token-budget-per-minute", type=int, default=180000,
                         help="The real binding constraint for grouped extraction specifically "
                              "(found live: the free tier caps input tokens/minute at 250,000; "
                              "each group call re-sends the full contract text, so 7 concurrent "
                              "group calls burns through that budget almost immediately without "
                              "this). Default leaves a safety margin under the observed real cap.")
    parser.add_argument("--model", type=str, default="gemini-flash-lite-latest",
                         help="Must match the single-pass baseline's model for a fair comparison "
                              "(the v3 checkpoint was run on gemini-flash-lite-latest).")
    args = parser.parse_args()

    print("Loading corpus, ground truth, checkpoint, benchmark rows...")
    corpus = json.load(open(CORPUS_JSON, encoding="utf-8"))
    risk_gt = json.load(open(RISK_CATEGORIES_JSON, encoding="utf-8"))
    checkpoint = load_checkpoint_entries()
    benchmark_rows = load_benchmark_rows()
    benchmark_rows_by_title = {
        (r["Filename"][:-4] if r["Filename"].lower().endswith(".pdf") else r["Filename"]): r
        for r in benchmark_rows
    }

    sample_filenames, unmet_need = select_stratified_sample(
        checkpoint, risk_gt, args.sample_size, args.min_per_category
    )
    print(f"\nSelected {len(sample_filenames)} contracts.")
    if any(v > 0 for v in unmet_need.values()):
        short = {k: v for k, v in unmet_need.items() if v > 0}
        print(f"Coverage target not fully met (corpus doesn't have enough real examples) for: {short}")

    sample_path = os.path.join(BENCHMARK_DIR, "pilot_grouped_extraction_sample.json")
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(sample_filenames, f, indent=2)
    print(f"Sample saved to {sample_path}\n")

    print(f"Running grouped extraction (model={args.model}, rpm_limit={args.rpm_limit}, "
          f"token_budget_per_minute={args.token_budget_per_minute})...\n")
    grouped_results = asyncio.run(
        run_grouped_on_sample(sample_filenames, corpus, args.model,
                               args.token_budget_per_minute, args.rpm_limit)
    )

    raw_path = os.path.join(BENCHMARK_DIR, "pilot_grouped_extraction_results.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(grouped_results, f, indent=2)
    print(f"\nRaw grouped-extraction results saved to {raw_path}")

    # Single-pass baseline: reused verbatim from the checkpoint, zero new calls.
    single_pass_entries = {fn: checkpoint[fn] for fn in sample_filenames if fn in checkpoint}
    grouped_entries = {fn: grouped_results[fn] for fn in sample_filenames if fn in grouped_results}

    single_counts = aggregate(single_pass_entries, checkpoint, corpus, risk_gt, benchmark_rows_by_title)
    grouped_counts = aggregate(grouped_entries, checkpoint, corpus, risk_gt, benchmark_rows_by_title)

    print("\n" + "=" * 100)
    print(f"{'Category':<38}{'Grp':<4}{'SP F1':>8}{'GR F1':>8}{'Δ':>8}{'  SP(tp/fp/fn)':>16}{'  GR(tp/fp/fn)':>16}")
    print("=" * 100)

    def group_label(cat):
        if cat in GROUP_A:
            return "A"
        if cat in GROUP_B:
            return "B"
        if cat in GROUP_C:
            return "C"
        if cat in CONTROL_CATEGORIES:
            return "ctrl"
        return ""

    all_cats = TARGET_CATEGORIES + CONTROL_CATEGORIES
    deltas = []
    for cat in all_cats:
        sc = single_counts.get(cat, {"tp": 0, "fp": 0, "fn": 0})
        gc = grouped_counts.get(cat, {"tp": 0, "fp": 0, "fn": 0})
        _, _, sf1 = prf1(sc)
        _, _, gf1 = prf1(gc)
        delta = gf1 - sf1
        deltas.append((cat, delta, sf1, gf1))
        print(f"{cat:<38}{group_label(cat):<4}{sf1:>8.3f}{gf1:>8.3f}{delta:>+8.3f}"
              f"  {sc['tp']}/{sc['fp']}/{sc['fn']:<10}  {gc['tp']}/{gc['fp']}/{gc['fn']}")

    print("=" * 100)
    target_deltas = [d for cat, d, _, _ in deltas if cat in TARGET_CATEGORIES]
    control_deltas = [d for cat, d, _, _ in deltas if cat in CONTROL_CATEGORIES]
    print(f"\nMean Δ F1 across {len(TARGET_CATEGORIES)} target (weak) categories: {statistics.mean(target_deltas):+.3f}")
    print(f"Mean Δ F1 across {len(CONTROL_CATEGORIES)} control (already-strong) categories: {statistics.mean(control_deltas):+.3f}")

    # Cost/latency
    call_counts = [len(CATEGORY_GROUPS) for _ in grouped_entries]
    elapsed_times = [grouped_results[fn]["elapsed_seconds"] for fn in grouped_entries]
    total_group_failures = sum(len(grouped_results[fn]["group_failures"]) for fn in grouped_entries)
    print(f"\nGrouped approach: {sum(call_counts)} real LLM calls across {len(grouped_entries)} contracts "
          f"({len(CATEGORY_GROUPS)} calls/contract, always - no conditional skipping).")
    print(f"Single-pass v3 baseline (production today): 1 call/contract typical, up to 2 when the "
          f"fallback pass fires (see docs/EVALUATION.md §4c) - roughly {len(CATEGORY_GROUPS)}x-{len(CATEGORY_GROUPS)//2}x "
          f"fewer calls than grouped.")
    if elapsed_times:
        print(f"Grouped wall-clock latency per contract: mean {statistics.mean(elapsed_times):.1f}s, "
              f"median {statistics.median(elapsed_times):.1f}s, max {max(elapsed_times):.1f}s "
              f"(all 7 groups run concurrently, bounded by llm_call_semaphore's default concurrency of 5).")
    print(f"Group-level failures across the whole run: {total_group_failures} "
          f"(out of {len(grouped_entries) * len(CATEGORY_GROUPS)} total group calls).")

    summary = {
        "model": args.model,
        "sample_size": len(sample_filenames),
        "sample_filenames": sample_filenames,
        "unmet_coverage": {k: v for k, v in unmet_need.items() if v > 0},
        "per_category": {
            cat: {"single_pass_f1": sf1, "grouped_f1": gf1, "delta": gf1 - sf1,
                  "single_pass_counts": single_counts.get(cat, {"tp": 0, "fp": 0, "fn": 0}),
                  "grouped_counts": grouped_counts.get(cat, {"tp": 0, "fp": 0, "fn": 0})}
            for cat, delta, sf1, gf1 in deltas
        },
        "mean_delta_target_categories": statistics.mean(target_deltas),
        "mean_delta_control_categories": statistics.mean(control_deltas),
        "grouped_calls_per_contract": len(CATEGORY_GROUPS),
        "grouped_total_calls": sum(call_counts),
        "grouped_latency_seconds": {
            "mean": statistics.mean(elapsed_times) if elapsed_times else None,
            "median": statistics.median(elapsed_times) if elapsed_times else None,
            "max": max(elapsed_times) if elapsed_times else None,
        },
        "grouped_group_failures_total": total_group_failures,
    }
    summary_path = os.path.join(BENCHMARK_DIR, "pilot_grouped_extraction_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
