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

## 4c. Weak-category accuracy pass — real prompt/fallback-pass fixes (PROMPT_VERSION v3)

Follow-up work targeting the 20 categories flagged in §4b as below 0.30 F1. Each was individually root-caused from the real per-contract predictions already stored in the 497-contract checkpoint (`extraction_eval_checkpoint_risk-categories-flash-lite-497.jsonl`) — zero new LLM calls needed for the diagnosis, the same technique that found the Expiration Date issue in §4a.

**Root-cause finding: no generalizable matcher bug, unlike §4a's date-parsing case.** Every "found something, didn't match" case inspected across the highest-unmatched-ratio categories (Change Of Control, Source Code Escrow, Minimum Commitment) showed the model's prediction and CUAD's gold span were genuinely *different sentences* about the same broad topic (e.g. two valid non-disparagement clauses, one per party, CUAD picks one canonical span), not the same content in a matcher-unfriendly format. `text_match()`'s word-boundary containment check already catches true verbatim-subset cases regardless of length. **`evaluate_extraction.py`'s matcher was not changed** — this is reported honestly rather than forcing a fix where the data didn't support one.

The 20 categories split into three groups, each getting a different, targeted treatment in `backend/agents/llm_extraction_service.py`:

| Group | Categories | Diagnosis | Treatment |
|---|---|---|---|
| **A — precision fix** | Third Party Beneficiary, Change Of Control | The model finds *a* clause but the wrong one. Third Party Beneficiary: 103/497 contracts got a false positive, almost all boilerplate "no third party beneficiaries" *disclaimer* clauses matched as if they granted rights. Change Of Control: 18/96 false negatives were the model quoting the bare *definition* of "Change of Control" instead of the operative consequence clause CUAD's gold span points to. | One-line hint appended next to the category name in the main 41-category prompt, disambiguating exactly what the category does and doesn't mean (`_CATEGORY_HINTS`). |
| **C — recall gap** | Non-Disparagement, Most Favored Nation, Source Code Escrow, Rofr/Rofo/Rofn, Ip Ownership Assignment, Revenue/Profit Sharing, Minimum Commitment, Exclusivity, Uncapped Liability, Price Restrictions | Ordinary recall gap — the model already finds some real examples (TP > 0 in every case) but under-recalls. Not a case of the model ignoring the category outright. | Same one-line-hint treatment as Group A (`_CATEGORY_HINTS`) — proportionate to "needs sharper guidance," not "needs a second call." |
| **B — near-zero engagement** | Volume Restriction, Competitive Restriction Exception, Unlimited/All-You-Can-Eat-License, Joint Ip Ownership, Affiliate License-Licensee, Affiliate License-Licensor, Covenant Not To Sue, Post-Termination Services | The model essentially never attempted these at all — 0-1 true positives *and* near-zero false positives each, across all 497 contracts (not wrong guesses, no guesses). Volume Restriction and Unlimited/All-You-Can-Eat-License showed exactly 0 TP and 0 FP. A hint competing for attention inside an already-dense 41-category prompt was judged unlikely to fix "never engages with this category at all." | A second, narrower, example-backed LLM call (`FALLBACK_CATEGORIES` / `_run_fallback_pass`), fired **only when the primary pass found none of these 8** — most contracts genuinely don't contain these rare clause types, so the common case still costs one LLM call, not two. Mirrors the deterministic-table-vs-LLM-reasoned split already used in `PolicyEvaluationService`, adapted here as "broad pass vs. targeted narrow pass." |

**Dilution guard**: neither hint list touches the 5 metadata columns, Governing Law, or any category that was already scoring reasonably — added guidance is scoped exclusively to the 20 categories that needed it, specifically to avoid degrading the categories that already worked by crowding the prompt (verified below — no regression on metadata).

### Re-measurement: 243 of 510 contracts (`extraction_eval_results_risk-categories-flash-lite-497-v3.json`, `gemini-flash-lite-latest`)

**This is a partial re-run, not yet the full corpus** — the free tier's real per-day cap on `gemini-flash-lite-latest` turned out to be 500 requests/day (distinct from its 15 requests/minute cap, both discovered live while running this pass), and the fallback pass roughly doubles request volume on contracts that trigger it, so one day's quota now covers roughly half the 510-contract corpus instead of all of it. The run is checkpoint-resumable exactly like every other run in this project; the remaining ~267 contracts complete on a subsequent day's quota without re-spending anything already measured. The comparison below uses ratios (precision/recall/F1), which are far less sensitive to the sample-size difference (243 vs. 497) than raw counts — but categories with small absolute gold-positive counts still carry real sampling noise, called out explicitly below.

**Before/after on the 20 targeted categories:**

| Category | Group | OLD F1 (497 contracts) | NEW F1 (243 contracts) | Δ |
|---|---|---|---|---|
| Joint Ip Ownership | B | 0.043 | 0.537 | **+0.494** |
| Third Party Beneficiary | A | 0.250 | 0.647 | **+0.397** |
| Affiliate License-Licensee | B | 0.034 | 0.387 | **+0.353** |
| Competitive Restriction Exception | B | 0.000 | 0.286 | **+0.286** |
| Revenue/Profit Sharing | C | 0.169 | 0.444 | **+0.275** |
| Uncapped Liability | C | 0.212 | 0.447 | **+0.235** |
| Change Of Control | A | 0.271 | 0.500 | **+0.229** |
| Exclusivity | C | 0.255 | 0.477 | **+0.222** |
| Unlimited/All-You-Can-Eat-License | B | 0.000 | 0.200 | +0.200 |
| Ip Ownership Assignment | C | 0.189 | 0.381 | +0.192 |
| Rofr/Rofo/Rofn | C | 0.196 | 0.360 | +0.164 |
| Post-Termination Services | B | 0.011 | 0.175 | +0.164 |
| Minimum Commitment | C | 0.242 | 0.327 | +0.085 |
| Volume Restriction | B | 0.000 | 0.080 | +0.080 |
| Non-Disparagement | C | 0.298 | 0.372 | +0.074 |
| Price Restrictions | C | 0.000 | 0.000 | +0.000 |
| Affiliate License-Licensor | B | 0.000 | 0.000 | +0.000 |
| Covenant Not To Sue | B | 0.020 | 0.000 | −0.020 |
| Most Favored Nation | C | 0.242 | 0.143 | −0.099 |
| Source Code Escrow | C | 0.222 | 0.095 | −0.127 |

**15 of 20 improved, several substantially; 2 unchanged at 0.00; 3 got worse — reported honestly rather than only showing the wins:**

- **Covenant Not To Sue** (Group B): fallback pass didn't help — went from 1 TP (out of 97 gold-positive) to 0 (out of 60). One of 8 fallback categories where the intervention didn't land; F1 was already near-zero either way.
- **Most Favored Nation** (Group C): 4→1 TP, but gold-positive count in this partial sample is only 12 (vs. 28 in the full old run) — a 1-example swing on this small a base is within sampling noise for a genuinely rare category, not strong evidence the hint hurt.
- **Source Code Escrow** (Group C): the clearest real regression — FP jumped from 3 to 18 while FN dropped from 11 to 1. The hint pushed recall up sharply but at a real precision cost: the model now over-triggers on escrow-adjacent language that isn't a true match. Worth narrowing this specific hint if revisited.

**No dilution regression on the categories intentionally left untouched** (the explicit concern raised before implementing):

| Category | OLD F1 | NEW F1 | Δ |
|---|---|---|---|
| Document Name | 0.990 | 0.990 | +0.000 |
| Parties | 0.975 | 0.985 | +0.010 |
| Agreement Date | 0.766 | 0.739 | −0.027 |
| Effective Date | 0.679 | 0.662 | −0.017 |
| Expiration Date | 0.358 | 0.402 | +0.044 |
| Governing Law | 0.978 | 0.986 | +0.008 |

All within normal sample-to-sample variance (compare to the ±0.02-0.03 spread already documented between the two independent 497-contract runs in §4). The other 15 risk categories not touched by any hint or fallback pass also moved within a similar noise band (mostly small positive drift, attributable to the different 243-vs-497 contract sample composition, not the prompt change) — full numbers in the raw results JSON.

**Averages, all 36 risk-relevant categories (not just the 20 targeted):**

| | Avg Precision | Avg Recall | Avg F1 |
|---|---|---|---|
| OLD (497 contracts, v2 prompt) | 0.631 | 0.269 | 0.323 |
| NEW (243 contracts, v3 prompt + fallback) | 0.644 | 0.373 | **0.435** |

Categories below 0.30 F1: **20/36 → 9/36**. Categories flat at 0.00 F1: **5/36 → 3/36** (Price Restrictions, Affiliate License-Licensor, Covenant Not To Sue remain unfixed).

**Full new 41-category table** (243/510 contracts, `gemini-flash-lite-latest`, PROMPT_VERSION v3), sorted by F1 descending:

| Type | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| Document Name | 1.00 | 0.98 | 0.99 | 238 | 0 | 5 |
| Governing Law | 0.98 | 0.99 | 0.99 | 213 | 5 | 1 |
| Parties | 1.00 | 0.97 | 0.98 | 236 | 0 | 7 |
| Anti-Assignment | 0.98 | 0.72 | 0.83 | 133 | 3 | 52 |
| Agreement Date | 0.96 | 0.60 | 0.74 | 133 | 5 | 89 |
| Notice Period To Terminate Renewal | 0.71 | 0.73 | 0.72 | 40 | 16 | 15 |
| No-Solicit Of Employees | 0.84 | 0.61 | 0.71 | 16 | 3 | 10 |
| Renewal Term | 0.70 | 0.68 | 0.69 | 61 | 26 | 29 |
| Insurance | 0.73 | 0.64 | 0.68 | 48 | 18 | 27 |
| Termination For Convenience | 0.70 | 0.65 | 0.67 | 63 | 27 | 34 |
| Cap On Liability | 0.92 | 0.52 | 0.67 | 71 | 6 | 65 |
| Effective Date | 0.80 | 0.57 | 0.66 | 105 | 27 | 80 |
| Third Party Beneficiary | 0.58 | 0.73 | 0.65 | 11 | 8 | 4 |
| Audit Rights | 0.60 | 0.62 | 0.61 | 69 | 46 | 42 |
| License Grant | 0.95 | 0.38 | 0.54 | 55 | 3 | 90 |
| Joint Ip Ownership | 0.85 | 0.39 | 0.54 | 11 | 2 | 17 |
| Non-Compete | 0.73 | 0.42 | 0.54 | 27 | 10 | 37 |
| Non-Transferable License | 0.80 | 0.37 | 0.51 | 32 | 8 | 54 |
| No-Solicit Of Customers | 0.86 | 0.35 | 0.50 | 6 | 1 | 11 |
| Change Of Control | 0.73 | 0.38 | 0.50 | 25 | 9 | 41 |
| Exclusivity | 0.78 | 0.34 | 0.48 | 31 | 9 | 59 |
| Uncapped Liability | 0.70 | 0.33 | 0.45 | 21 | 9 | 43 |
| Revenue/Profit Sharing | 0.93 | 0.29 | 0.44 | 26 | 2 | 63 |
| Liquidated Damages | 0.89 | 0.30 | 0.44 | 8 | 1 | 19 |
| Expiration Date | 0.83 | 0.27 | 0.40 | 44 | 9 | 122 |
| Warranty Duration | 0.47 | 0.35 | 0.40 | 9 | 10 | 17 |
| Affiliate License-Licensee | 0.48 | 0.32 | 0.39 | 12 | 13 | 25 |
| Ip Ownership Assignment | 0.56 | 0.29 | 0.38 | 20 | 16 | 49 |
| Non-Disparagement | 0.40 | 0.35 | 0.37 | 8 | 12 | 15 |
| Rofr/Rofo/Rofn | 1.00 | 0.22 | 0.36 | 9 | 0 | 32 |
| Minimum Commitment | 0.53 | 0.24 | 0.33 | 17 | 15 | 55 |
| Irrevocable Or Perpetual License | 0.75 | 0.19 | 0.30 | 6 | 2 | 26 |
| Competitive Restriction Exception | 0.58 | 0.19 | 0.29 | 7 | 5 | 30 |
| Unlimited/All-You-Can-Eat-License | 1.00 | 0.11 | 0.20 | 1 | 0 | 8 |
| Post-Termination Services | 0.69 | 0.10 | 0.17 | 9 | 4 | 81 |
| Most Favored Nation | 0.50 | 0.08 | 0.14 | 1 | 1 | 11 |
| Source Code Escrow | 0.05 | 0.50 | 0.10 | 1 | 18 | 1 |
| Volume Restriction | 0.22 | 0.05 | 0.08 | 2 | 7 | 39 |
| Price Restrictions | 0.00 | 0.00 | 0.00 | 0 | 4 | 10 |
| Affiliate License-Licensor | 0.00 | 0.00 | 0.00 | 0 | 5 | 11 |
| Covenant Not To Sue | 0.00 | 0.00 | 0.00 | 0 | 2 | 60 |

**Reproducing / completing this run**:

```bash
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py \
  --model gemini-flash-lite-latest --run-name risk-categories-flash-lite-497-v3 --rpm-limit 8
```
Resumes automatically from the existing checkpoint. `--rpm-limit` paces real HTTP requests (new in this pass — `_RpmLimiter` in `evaluate_extraction.py`) to stay under the free tier's per-minute cap; transient errors (503s, timeouts, and the per-minute token-quota window) now retry automatically instead of ending the run, while a genuine per-day quota exhaustion still fails fast by design (`_extract_with_transient_retry`, reusing `execution_engine.py`'s existing `_is_quota_exhausted`).

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

### 5c. v3-prompt confirmation spot-check (`extraction_eval_results_risk-categories-2.5-flash-v3.json`)

Same 20/day quota wall stopped this at **5 of 90 contracts** too, and — because the stratified sample is drawn fresh per `--run-name` — this is a *different* 5 contracts than §5b, not a rerun of the same ones. Not a valid before/after pair; included only because it's a real artifact from this pass. On Group A's two categories specifically: Change Of Control went 0.667→1.000 (1 more TP, consistent with §4c's direction); Third Party Beneficiary went 0.667→0.000 (0 TP, 3 FP in this specific 5-contract draw) — the opposite direction from §4c's 243-contract flash-lite result (0.250→0.647). At n=5 this is expected sampling noise, not a contradiction of the larger, more reliable flash-lite measurement — **§4c's 243-contract sample is the result to trust here, not this spot-check.** A larger production-model sample (blocked on the same 20/day cap) is needed before drawing any real conclusion about `gemini-2.5-flash` specifically.

## 6. Grounding, hallucination rate, cost, and latency

Three previously-log-only or missing observability gaps were closed in a later pass (`docs/CAPSTONE_SUMMARY.md` §10, AI-engineering-depth findings #11-13):

- **Hallucination/grounding rate** (finding #12): `GET /api/monitoring/llm-usage`'s `hallucination_tracking` field and `GET /metrics`'s `hallucination_rate{category=...}` gauge now expose two real rates — `clause_extraction` (extracted clauses whose text couldn't be located verbatim in the source contract, i.e. `grounded: false`) and `policy_citation` (LLM-cited policy `rule_id`s that didn't actually exist in the rules offered, discarded by `PolicyEvaluationService.evaluate_clause`). Both are Redis-backed shared counters (`backend/shared/monitoring/hallucination_tracker.py`), visible regardless of whether the extraction/evaluation call happened in the `backend` or `worker` container.
- **Cost/latency on the primary path** (finding #13): `LLMExtractionService.extract_clauses` and `PolicyEvaluationService.evaluate_clause` — the two calls that dominate real per-contract cost/latency — now carry `@track_latency` (`shared/monitoring/latency_tracker.py`), the same Redis-backed, cross-process-visible pattern as the LLM usage/hallucination trackers above (not the in-process `@track_performance` the secondary CUAD-mitigation tools use). `GET /metrics`'s `operation_latency_p50_ms`/`p95_ms` gauges report real p50/p95 duration for the primary analysis path regardless of whether the call happened in the `backend` or `worker` container.
- **No production numbers exist yet for either.** Both are freshly wired counters starting at zero — this report does not fabricate a rate or a p95 figure ahead of real traffic. Once the platform has real usage, the two endpoints above are where to look; this section should be updated with real numbers at that point rather than left as a placeholder indefinitely.

## 7. Reproducing these numbers

```bash
# Metadata-only run (5 columns)
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py

# Risk-category ground truth prep (one-time, sources the other 36 columns)
python research/benchmark/prepare_risk_category_ground_truth.py

# Full 41-category run
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py --run-name risk-categories-<label>

# Full 41-category run, rate-limited (needed for gemini-flash-lite-latest,
# whose free-tier per-minute cap this script's natural request rate exceeds
# once the fallback pass - §4c - is in play)
GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py \
  --model gemini-flash-lite-latest --run-name <label> --rpm-limit 8
```

Results land in `research/benchmark/extraction_eval_results_<run_name>.json`; a matching `*_checkpoint.jsonl` allows resuming a quota-interrupted run rather than restarting from zero.

## 8. Known limitations of this evaluation itself

- **Presence/content-match, not span accuracy.** A clause matched on fuzzy token overlap is scored correct even if the extracted span differs from the gold span in minor ways. Span-level grounding is checked separately (and deterministically) by `_find_span`, not by this benchmark.
- **`gemini-flash-lite-latest` is a rolling alias**, not a pinned model snapshot — the two 497-contract runs in §4 were not run against a guaranteed-identical model version, which is the most likely explanation for their small numeric differences on the same 5 metadata columns.
- **The risk-category confirmation samples on the production model (§5b, §5c) are too small (n=5 each) to trust**, and are two different 5-contract draws rather than a before/after pair on the same contracts — flagged explicitly rather than presented alongside the real numbers without caveat.
- **§4c's weak-category re-measurement is a 243/510-contract partial sample**, not the full corpus — blocked on `gemini-flash-lite-latest`'s real per-day request quota (discovered to be 500/day live during this pass), which the fallback pass's extra calls now consume roughly twice as fast as before. The remaining ~267 contracts are queued to complete via the same checkpoint-resume mechanism on a later day's quota; the qualitative result (15/20 targeted categories improved, no metadata regression) is unlikely to reverse, but exact F1 values may shift slightly once the full corpus is measured.
- **9 of 36 risk-relevant categories remain below 0.30 F1** after the §4c pass (down from 20/36) — Price Restrictions, Affiliate License-Licensor, and Covenant Not To Sue remain at a flat 0.00 F1 with no real fix found yet; Source Code Escrow got measurably worse (a real, reported regression, not a rounding artifact). Some of the remaining gap may be genuine corpus rarity rather than model failure, which would change the right remediation (more ground truth, not a better prompt) — this is `docs/CAPSTONE_SUMMARY.md` §9 item 9's still-open thread.
