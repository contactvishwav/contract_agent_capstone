# Evaluation Report

Standalone, skimmable record of how extraction accuracy in this platform was actually measured, what the numbers are, and what's still weak — pulled from the raw benchmark artifacts in `research/benchmark/`, not restated from memory. `docs/CAPSTONE_SUMMARY.md` §3 and §4 item 12 narrate the same work as part of the engagement history; this document exists so the accuracy story doesn't require reading that whole retrospective to find.

## 1. What's being measured

`LLMExtractionService.extract_clauses()` (`backend/agents/llm_extraction_service.py`) is scored against real ground truth from the [CUAD dataset](https://www.atticusprojectai.org/cuad) (`theatticusproject/cuad-qa` on HuggingFace), across all 41 CUAD clause categories, split into two groups:

- **5 metadata columns** — Document Name, Parties, Agreement Date, Effective Date, Expiration Date. Ground truth for these comes from `research/benchmark/extraction_benchmark.csv`.
- **36 risk-relevant columns** — everything else (Cap On Liability, Non-Compete, Termination For Convenience, Ip Ownership Assignment, etc.) — the categories a risk assessment actually depends on. `extraction_benchmark.csv` doesn't cover these; `prepare_risk_category_ground_truth.py` sources them from the same underlying dataset (one row per contract/category question, `answers` = verbatim gold text spans, empty if that category doesn't apply).

Both groups are run through the same evaluator, `research/benchmark/evaluate_extraction.py`.

## 2. Methodology

**Presence + content-match scoring, not span-level IoU.** The ground truth has no character offsets to compare spans against — it's gold text values per (contract, category), not annotated spans. For each (contract, clause_type):

- **TP**: ground truth has a value AND the model extracted a clause of that type whose text matches (fuzzy/date-aware) at least one gold value.
- **FN**: ground truth has a value but no predicted clause of that type matched.
- **FP**: the model extracted a clause of that type but ground truth has no value for it in this contract.

Precision/recall/F1 are the standard aggregates over TP/FP/FN, per type.

- **Text matching**: normalized token-overlap ≥ 0.6, or substring containment.
- **Date matching**: both sides parsed to a calendar date (several common formats plus a fuzzy substring search) and compared; falls back to text matching if either side fails to parse.

This means a paraphrased-but-correct clause can still count as a match (fuzzy overlap), and a clause found but not linked to any specific ground-truth span is not separately penalized for span accuracy — the metric answers "did the model find the right *value*," not "did it find the exact *characters*." Offsets themselves (used for the `grounded` flag surfaced in the product) are verified separately and deterministically by `LLMExtractionService._find_span`, not part of this benchmark's scoring.

## 3. The quota-forced model substitution

The Gemini free tier caps `gemini-2.5-flash` (the production model) at **20 requests/day/project** — nowhere near enough for a 497-contract run (497+ LLM calls). Full-scale runs use `gemini-flash-lite-latest` instead; this substitution is explicit in every benchmark artifact's own `model` field, never silently absorbed into "the benchmark." Smaller confirmation samples on the real production model (`gemini-2.5-flash`) exist alongside the full-scale Flash-Lite runs specifically to check whether the production model's numbers track the same shape — see §5.

## 4. Results — 497-contract scale (`gemini-flash-lite-latest`)

Two independent full-scale runs exist, evaluated at different times against the same 497-contract corpus (13 of the original 510 skipped for missing text in both runs — same list). Their numbers differ slightly for the 5 metadata columns they both score, most likely from `gemini-flash-lite-latest` being a rolling model alias (not a pinned snapshot) plus normal LLM output variance between runs, rather than any change to the matching logic itself. Both are reported rather than picked, per this project's own "confirmed, not assumed" discipline.

### 4a. `extraction_eval_results_flash-lite-497.json` (metadata-only run)

| Type | Precision | Recall | F1 |
|---|---|---|---|
| Document Name | 1.00 | 0.99 | 0.99 |
| Parties | 1.00 | 0.95 | 0.98 |
| Agreement Date | 0.98 | 0.63 | 0.77 |
| Effective Date | 0.90 | 0.65 | 0.76 |
| Expiration Date | 0.91 | 0.15 | 0.26 |

**Root-cause breakdown on the weakest column (Expiration Date)**: of 274 misses, 96% (263) were the model finding nothing at all for that contract; only 4% (11) were found-but-unmatched — the date-matching logic itself works, and the gap is model recall, not the matcher.

### 4b. `extraction_eval_results_risk-categories-flash-lite-497.json` (all 41 categories, one run)

Same 497 contracts, same model, run later alongside the 36 new risk categories — includes its own independently re-scored numbers for the same 5 metadata columns:

| Type | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| Document Name | 1.00 | 0.98 | 0.99 | 487 | 0 | 10 |
| Parties | 1.00 | 0.95 | 0.97 | 472 | 0 | 24 |
| Agreement Date | 0.97 | 0.64 | 0.77 | 287 | 10 | 165 |
| Effective Date | 0.81 | 0.58 | 0.68 | 203 | 47 | 145 |
| Expiration Date | 0.81 | 0.23 | 0.36 | 74 | 17 | 248 |

**All 36 risk-relevant categories, same run, sorted by F1 descending:**

| Type | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| Governing Law | 0.97 | 0.99 | 0.98 | 423 | 15 | 4 |
| No-Solicit Of Employees | 0.91 | 0.74 | 0.82 | 42 | 4 | 15 |
| Anti-Assignment | 0.92 | 0.73 | 0.81 | 266 | 24 | 100 |
| Notice Period To Terminate Renewal | 0.67 | 0.72 | 0.69 | 78 | 39 | 30 |
| Renewal Term | 0.69 | 0.66 | 0.68 | 113 | 50 | 59 |
| Insurance | 0.86 | 0.54 | 0.66 | 86 | 14 | 73 |
| Cap On Liability | 0.88 | 0.40 | 0.55 | 107 | 15 | 160 |
| Audit Rights | 0.61 | 0.46 | 0.53 | 96 | 60 | 112 |
| Non-Compete | 0.70 | 0.40 | 0.51 | 45 | 19 | 69 |
| Non-Transferable License | 0.80 | 0.33 | 0.46 | 43 | 11 | 89 |
| Termination For Convenience | 0.74 | 0.32 | 0.45 | 57 | 20 | 121 |
| Liquidated Damages | 0.84 | 0.27 | 0.41 | 16 | 3 | 43 |
| No-Solicit Of Customers | 0.56 | 0.32 | 0.41 | 10 | 8 | 21 |
| Irrevocable Or Perpetual License | 0.85 | 0.25 | 0.39 | 17 | 3 | 50 |
| Warranty Duration | 0.46 | 0.25 | 0.32 | 18 | 21 | 55 |
| License Grant | 0.92 | 0.18 | 0.31 | 46 | 4 | 204 |
| Non-Disparagement | 0.64 | 0.19 | 0.30 | 7 | 4 | 29 |
| Change Of Control | 0.55 | 0.18 | 0.27 | 21 | 17 | 96 |
| Exclusivity | 0.71 | 0.15 | 0.26 | 27 | 11 | 147 |
| Third Party Beneficiary | 0.16 | 0.63 | 0.25 | 19 | 103 | 11 |
| Most Favored Nation | 0.80 | 0.14 | 0.24 | 4 | 1 | 24 |
| Minimum Commitment | 0.67 | 0.15 | 0.24 | 24 | 12 | 138 |
| Source Code Escrow | 0.40 | 0.15 | 0.22 | 2 | 3 | 11 |
| Uncapped Liability | 0.58 | 0.13 | 0.21 | 14 | 10 | 94 |
| Rofr/Rofo/Rofn | 0.90 | 0.11 | 0.20 | 9 | 1 | 73 |
| Ip Ownership Assignment | 0.50 | 0.12 | 0.19 | 14 | 14 | 106 |
| Revenue/Profit Sharing | 0.94 | 0.09 | 0.17 | 15 | 1 | 146 |
| Joint Ip Ownership | 1.00 | 0.02 | 0.04 | 1 | 0 | 44 |
| Affiliate License-Licensee | 1.00 | 0.02 | 0.03 | 1 | 0 | 57 |
| Covenant Not To Sue | 0.50 | 0.01 | 0.02 | 1 | 1 | 96 |
| Post-Termination Services | 1.00 | 0.01 | 0.01 | 1 | 0 | 174 |
| Competitive Restriction Exception | 0.00 | 0.00 | 0.00 | 0 | 0 | 74 |
| Price Restrictions | 0.00 | 0.00 | 0.00 | 0 | 3 | 15 |
| Volume Restriction | 0.00 | 0.00 | 0.00 | 0 | 0 | 82 |
| Affiliate License-Licensor | 0.00 | 0.00 | 0.00 | 0 | 2 | 23 |
| Unlimited/All-You-Can-Eat-License | 0.00 | 0.00 | 0.00 | 0 | 0 | 17 |

**Averages:**

| | Avg Precision | Avg Recall | Avg F1 |
|---|---|---|---|
| 5 metadata columns | 0.92 | 0.68 | 0.75 |
| 36 risk-relevant columns | 0.63 | 0.27 | 0.32 |

**Takeaway**: accurate metadata extraction does not imply accurate risk extraction. 5 of 36 risk categories scored a flat 0.00 F1; 20 of 36 scored below 0.30. Only 3 (Governing Law, No-Solicit Of Employees, Anti-Assignment) matched metadata-tier quality. Across nearly every weak category, **recall is the dominant gap, not precision** — the model is usually right when it does find something (many categories still show precision ≥ 0.6-0.9 even at F1 ≈ 0.2), it's just not finding the clause at all in most contracts where it exists. This is the same pattern as the Expiration Date root-cause finding in §4a, generalized: the gap is model recall on category presence, not the matching logic being too strict.

## 5. Confirmation samples on the production model (`gemini-2.5-flash`)

Both blocked on the same real constraint: the free tier's 20 requests/day/project quota. Both runs show `stopped_early: true`.

### 5a. Metadata columns, stratified sample (`extraction_eval_results_gemini-2.5-flash-stratified.json`)

**44 of 90 contracts complete** — large enough to be directionally meaningful, stratified toward the weak date types specifically to test whether the production model closes that gap faster:

| Type | Precision | Recall | F1 |
|---|---|---|---|
| Document Name | 1.00 | 1.00 | 1.00 |
| Parties | 1.00 | 1.00 | 1.00 |
| Agreement Date | 0.91 | 0.79 | 0.84 |
| Effective Date | 0.77 | 0.87 | 0.82 |
| Expiration Date | 0.76 | 0.48 | 0.59 |

Directionally consistent with §4a's root-cause analysis: the production model is meaningfully better on the weak date fields (Expiration Date F1 0.26 → 0.59) without any change to the matching logic — confirms the gap was model capability, not the matcher.

### 5b. Risk-relevant categories (`extraction_eval_results_risk-categories-2.5-flash.json`)

**Only 5 of 90 contracts complete.** Included for completeness since it's a real artifact, but explicitly **not statistically meaningful** at this sample size — most of the 41 categories show 0/0/0 (no ground-truth occurrence in these 5 contracts at all), and several "1.00" F1 scores are a single true positive with zero contradicting evidence either way. Treat this table as "nothing alarming showed up in 5 contracts," not as a confirmed result. Continuing this run to a larger sample (blocked on the same daily quota) is the natural next step — see `docs/CAPSTONE_SUMMARY.md` §9 item 1.

## 6. Grounding, hallucination rate, cost, and latency

Three previously-log-only or missing observability gaps were closed in a later pass (`docs/CAPSTONE_SUMMARY.md` §10, AI-engineering-depth findings #11-13):

- **Hallucination/grounding rate** (finding #12): `GET /api/monitoring/llm-usage`'s `hallucination_tracking` field and `GET /metrics`'s `hallucination_rate{category=...}` gauge now expose two real rates — `clause_extraction` (extracted clauses whose text couldn't be located verbatim in the source contract, i.e. `grounded: false`) and `policy_citation` (LLM-cited policy `rule_id`s that didn't actually exist in the rules offered, discarded by `PolicyEvaluationService.evaluate_clause`). Both are Redis-backed shared counters (`backend/shared/monitoring/hallucination_tracker.py`), visible regardless of whether the extraction/evaluation call happened in the `backend` or `worker` container.
- **Cost/latency on the primary path** (finding #13): `LLMExtractionService.extract_clauses` and `PolicyEvaluationService.evaluate_clause` — the two calls that dominate real per-contract cost/latency — now carry `@track_performance`, so `GET /api/monitoring/performance/clause_extraction` and `.../policy_evaluation` report real p50/p95 duration for the primary analysis path, not just the secondary CUAD-mitigation tools that already had this coverage.
- **No production numbers exist yet for either.** Both are freshly wired counters starting at zero — this report does not fabricate a rate or a p95 figure ahead of real traffic. Once the platform has real usage, the two endpoints above are where to look; this section should be updated with real numbers at that point rather than left as a placeholder indefinitely.

## 7. Reproducing these numbers

```bash
# Metadata-only run (5 columns)
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py

# Risk-category ground truth prep (one-time, sources the other 36 columns)
python research/benchmark/prepare_risk_category_ground_truth.py

# Full 41-category run
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py --run-name risk-categories-<label>
```

Results land in `research/benchmark/extraction_eval_results_<run_name>.json`; a matching `*_checkpoint.jsonl` allows resuming a quota-interrupted run rather than restarting from zero.

## 8. Known limitations of this evaluation itself

- **Presence/content-match, not span accuracy.** A clause matched on fuzzy token overlap is scored correct even if the extracted span differs from the gold span in minor ways. Span-level grounding is checked separately (and deterministically) by `_find_span`, not by this benchmark.
- **`gemini-flash-lite-latest` is a rolling alias**, not a pinned model snapshot — the two 497-contract runs in §4 were not run against a guaranteed-identical model version, which is the most likely explanation for their small numeric differences on the same 5 metadata columns.
- **The risk-category confirmation sample on the production model (§5b) is too small (n=5) to trust** — flagged explicitly rather than presented alongside the real numbers without caveat.
- **20 of 36 risk-relevant categories remain below 0.30 F1** with real, quantified gaps — this is not fixed by this document, only measured and reported. Some (e.g. Price/Volume Restriction, Joint IP Ownership) may be near-zero due to genuine corpus rarity in this 497-contract sample rather than model failure, which would change the right remediation (more ground truth, not a better prompt) — investigating this further is `docs/CAPSTONE_SUMMARY.md` §9 item 9, not yet done.
