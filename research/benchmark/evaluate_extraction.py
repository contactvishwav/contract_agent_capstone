"""
Benchmark evaluation for LLMExtractionService against real CUAD ground truth.

Ground truth source: research/benchmark/extraction_benchmark.csv, which
covers 5 of the 41 CUAD clause types (Document Name, Parties, Agreement
Date, Effective Date, Expiration Date) for 510 real contracts. It has no
contract full text and no character offsets - only "gold answer" values per
column - so this script:

  1. Loads contract full text from research/benchmark/cuad_contract_texts.json
     (produced by prepare_corpus.py from the same underlying CUAD-QA dataset,
     joined on filename).
  2. Runs LLMExtractionService.extract_clauses() once per contract (single
     LLM call, full contract text, all 41 candidate types).
  3. For the 5 clause types with ground truth, scores each contract as a
     detection problem: did the model find a matching value for this type in
     this contract?

Methodology / what precision, recall, and F1 mean here: this is per-contract,
per-type PRESENCE + CONTENT-MATCH scoring, not span-level IoU - the ground
truth has no offsets to compare spans against, and "Parties"/"Document Name"
ground truth is an unordered list of strings, not annotated spans. For each
(contract, clause_type):
  - TP: ground truth has a value AND the model extracted a clause of that
    type whose text matches (fuzzy/date-aware) at least one gold value.
  - FN: ground truth has a value but no predicted clause of that type matched.
  - FP: the model extracted a clause of that type but ground truth has no
    value for it in this contract.
Precision/recall/F1 are then the standard aggregates over TP/FP/FN, per type.

Text matching: normalized token-overlap / substring containment.
Date matching: both sides parsed to a calendar date (best-effort, several
common formats + a fuzzy substring search) and compared; falls back to text
matching if either side fails to parse.

Usage:
    GOOGLE_API_KEY=... python research/benchmark/evaluate_extraction.py [--limit N]

Requires GOOGLE_API_KEY (or GEMINI_API_KEY, since ChatGoogleGenerativeAI also
reads that) to be set - this makes real Gemini calls, one per contract
evaluated.
"""

import argparse
import ast
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO_ROOT, "backend", ".env"))

from backend.agents.llm_extraction_service import LLMExtractionService, CUADClauseType, get_default_llm  # noqa: E402
from backend.agents.llm_fallback_service import _is_quota_exhausted  # noqa: E402


def _extract_with_transient_retry(service, text, filename, max_attempts=3, backoff_seconds=8.0):
    """
    A real per-DAY quota exhaustion (_is_quota_exhausted, same helper
    llm_fallback_service.py uses to fail fast rather than retry into a 429)
    still stops this script's run immediately - that fail-fast behavior is
    intentional (retrying a per-day quota wall within one run's lifetime
    cannot succeed).

    Two other failure modes recover within seconds to a minute and get a
    few short retries here instead of ending the whole run over one bad
    contract:
      - transient infrastructure errors (a 503 UNAVAILABLE, a read timeout)
      - a PER-MINUTE quota window (observed: the free tier's 250K input-
        tokens/minute cap on gemini-flash-lite-latest, distinct from the
        per-day request cap - the API's own error names this
        "GenerateContentInputTokensPerModelPerMinute-FreeTier" and reports
        a retry delay of only a few seconds, since the window itself resets
        every minute)
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return service.extract_clauses(text, raise_on_error=True)
        except Exception as e:
            message = str(e)
            if _is_quota_exhausted(e) and "PerMinute" not in message:
                raise
            last_error = e
            if attempt < max_attempts:
                wait = 65.0 if "PerMinute" in message else backoff_seconds
                print(f"\n  transient error on {filename} (attempt {attempt}/{max_attempts}): {e} "
                      f"- retrying in {wait:.0f}s")
                time.sleep(wait)
    raise last_error


class _RpmLimiter:
    """
    Sliding-window requests-per-minute limiter, used only by this script.

    The weak-category accuracy pass added a conditional second (fallback)
    LLM call per contract for FALLBACK_CATEGORIES (llm_extraction_service.
    py), which roughly doubled this script's real request rate and tripped
    the free tier's per-minute cap on gemini-flash-lite-latest (observed:
    "limit: 15, model: gemini-3.5-flash-lite" - a *rate* limit, distinct
    from the per-day cap on the production model gemini-2.5-flash). Paces
    at the individual HTTP request level (not per-contract) since a single
    contract can make 1 or 2 real requests depending on whether the
    fallback pass fires.
    """

    def __init__(self, rpm: Optional[int]):
        self.rpm = rpm
        self._times = deque()

    def wait(self):
        if not self.rpm:
            return
        now = time.monotonic()
        while self._times and now - self._times[0] > 60.0:
            self._times.popleft()
        if len(self._times) >= self.rpm:
            sleep_for = 60.0 - (now - self._times[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while self._times and now - self._times[0] > 60.0:
                self._times.popleft()
        self._times.append(time.monotonic())

BENCHMARK_CSV = os.path.join(os.path.dirname(__file__), "extraction_benchmark.csv")
CORPUS_JSON = os.path.join(os.path.dirname(__file__), "cuad_contract_texts.json")
RISK_CATEGORIES_JSON = os.path.join(os.path.dirname(__file__), "risk_category_ground_truth.json")
BENCHMARK_DIR = os.path.dirname(__file__)


def results_path(run_name=None):
    suffix = f"_{run_name}" if run_name else ""
    return os.path.join(BENCHMARK_DIR, f"extraction_eval_results{suffix}.json")


def checkpoint_path(run_name=None):
    suffix = f"_{run_name}" if run_name else ""
    return os.path.join(BENCHMARK_DIR, f"extraction_eval_checkpoint{suffix}.jsonl")


def stratified_sample_path(run_name=None):
    suffix = f"_{run_name}" if run_name else ""
    return os.path.join(BENCHMARK_DIR, f"stratified_sample{suffix}.json")

COLUMN_TO_TYPE = {
    "Document Name": CUADClauseType.DOCUMENT_NAME,
    "Parties": CUADClauseType.PARTIES,
    "Agreement Date-Answer": CUADClauseType.AGREEMENT_DATE,
    "Effective Date-Answer": CUADClauseType.EFFECTIVE_DATE,
    "Expiration Date-Answer": CUADClauseType.EXPIRATION_DATE,
}
DATE_COLUMNS = {"Agreement Date-Answer", "Effective Date-Answer", "Expiration Date-Answer"}

# The other 36 CUAD categories (risk-relevant: Cap On Liability, Non-Compete,
# Termination For Convenience, Ip Ownership Assignment, etc.) - ground truth
# sourced by prepare_risk_category_ground_truth.py into
# risk_category_ground_truth.json, keyed by category name == CUADClauseType
# value (verified 1:1 against the dataset's own question categories). None
# of these are date-valued, so they're always scored via text_match, never
# the date-parsing path.
_METADATA_COLUMNS_COVERED = {"Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date"}
RISK_CATEGORY_TYPES = {
    ct.value: ct for ct in CUADClauseType if ct.value not in _METADATA_COLUMNS_COVERED
}

DATE_FORMATS = [
    "%m/%d/%y", "%m/%d/%Y",
    "%B %d, %Y", "%b %d, %Y",
    "%B %d %Y", "%b %d %Y",
    "%d %B %Y", "%d %b %Y",
    "%Y-%m-%d",
]

DATE_SEARCH_PATTERN = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b[A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+[A-Z][a-z]+\.?\s+\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
)

# Common legal phrasing: "the 8th day of May, 2014"
ORDINAL_DAY_PATTERN = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+)\.?,?\s+(\d{4})\b"
)


def _try_parse(candidate: str):
    candidate = candidate.replace(",", "").replace(".", "")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt.replace(",", "").replace(".", "")).date()
        except ValueError:
            continue
    return None


def parse_date_loose(text: str):
    """Best-effort extraction of a calendar date from free text."""
    text = (text or "").strip()
    if not text:
        return None
    direct = _try_parse(text)
    if direct:
        return direct
    ordinal = ORDINAL_DAY_PATTERN.search(text)
    if ordinal:
        day, month_name, year = ordinal.groups()
        parsed = _try_parse(f"{day} {month_name} {year}")
        if parsed:
            return parsed
    for match in DATE_SEARCH_PATTERN.findall(text):
        parsed = _try_parse(match)
        if parsed:
            return parsed
    return None


# --- Expiration Date relative-phrasing resolution -----------------------
#
# The smoke test showed most Expiration Date misses are cases where the
# model correctly finds the termination/expiration clause but expresses it
# as a relative duration ("one year from the Effective Date", "end of the
# current calendar year") rather than an absolute date. parse_date_loose
# alone can't resolve these. The functions below attempt to resolve such
# phrasing against the contract's OWN predicted Agreement/Effective Date
# (never against ground truth - that would leak gold data into scoring).
# If nothing matches, they return None: a miss stays a miss rather than a
# guess, per the explicit instruction not to guess.

EXPIRATION_KEYWORDS = ("expir", "end", "terminat", "until", "through", "last day")

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_DURATION_FROM_ANCHOR_RE = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\b(?:\s*\([^)]*\))?"
    r"\s+years?\s+(?:from(?:\s+and\s+after)?|after|following|thereafter)"
    r"(?:\s+(?:the\s+)?(effective date|agreement date|date hereof|date of this agreement|"
    r"execution date|date first (?:written|stated) above|initial date of execution))?"
)
_DURATION_GENERIC_RE = re.compile(
    r"for\s+(?:an?\s+)?(?:initial\s+)?(?:term|period)\s+of\s+"
    r"(\d+|" + "|".join(_NUMBER_WORDS) + r")\b(?:\s*\([^)]*\))?\s+years?"
)
_ANNIVERSARY_RE = re.compile(
    r"\b(" + "|".join(_ORDINAL_WORDS) + r")\s+anniversary of\s+(?:the\s+)?"
    r"(effective date|agreement date|date hereof|date of this agreement)"
)
_END_OF_YEAR_RE = re.compile(r"end of the (?:current )?(?:calendar )?year")


def _word_to_number(word: str):
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word)


def _add_years(d, years: int):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29 landing on a non-leap year
        return d.replace(month=2, day=28, year=d.year + years)


def _pick_anchor(ref, agreement_anchor, effective_anchor):
    if ref and "effective" in ref:
        return effective_anchor or agreement_anchor
    if ref:
        return agreement_anchor or effective_anchor
    return effective_anchor or agreement_anchor


def resolve_relative_expiration(text_lower: str, agreement_anchor, effective_anchor):
    """Resolve a small set of common relative-duration expiration phrasings
    against the contract's own predicted Agreement/Effective Date. Returns
    None if nothing recognizable is found - not a guess."""
    if _END_OF_YEAR_RE.search(text_lower):
        anchor = agreement_anchor or effective_anchor
        if anchor:
            return anchor.replace(month=12, day=31)

    m = _DURATION_FROM_ANCHOR_RE.search(text_lower)
    if m:
        n = _word_to_number(m.group(1))
        anchor = _pick_anchor(m.group(2), agreement_anchor, effective_anchor)
        if n and anchor:
            return _add_years(anchor, n)

    m = _DURATION_GENERIC_RE.search(text_lower)
    if m:
        n = _word_to_number(m.group(1))
        anchor = effective_anchor or agreement_anchor
        if n and anchor:
            return _add_years(anchor, n)

    m = _ANNIVERSARY_RE.search(text_lower)
    if m:
        n = _ORDINAL_WORDS.get(m.group(1))
        anchor = _pick_anchor(m.group(2), agreement_anchor, effective_anchor)
        if n and anchor:
            return _add_years(anchor, n)

    return None


def _dates_with_positions(text: str):
    """All (parsed_date, match_start_index) pairs found in text."""
    found = []
    for m in DATE_SEARCH_PATTERN.finditer(text):
        parsed = _try_parse(m.group(0))
        if parsed:
            found.append((parsed, m.start()))
    ordinal = ORDINAL_DAY_PATTERN.search(text)
    if ordinal:
        day, month_name, year = ordinal.groups()
        parsed = _try_parse(f"{day} {month_name} {year}")
        if parsed:
            found.append((parsed, ordinal.start()))
    return found


def parse_expiration_date(text: str, agreement_anchor, effective_anchor):
    """
    Best-effort resolution of an Expiration-Date-typed predicted clause into
    a calendar date. Tries, in order:
      1. If the clause contains multiple absolute dates (e.g. "commence on
         July 23, 2013 and expire on July 22, 2016"), pick the one closest
         to an expiration-signaling keyword rather than naively taking the
         first date in the string.
      2. A single unambiguous absolute date, if only one is present.
      3. Common relative-duration phrasings resolved against the contract's
         own predicted Agreement/Effective Date.
    Returns None (a miss) if none of these confidently apply.
    """
    text_lower = (text or "").lower()
    candidates = _dates_with_positions(text)

    if candidates:
        keyword_positions = [
            m.start() for kw in EXPIRATION_KEYWORDS for m in re.finditer(kw, text_lower)
        ]
        if keyword_positions:
            candidates.sort(key=lambda c: min(abs(c[1] - kp) for kp in keyword_positions))
            return candidates[0][0]
        if len(candidates) == 1:
            return candidates[0][0]
        # Multiple dates with no keyword to disambiguate which one is the
        # expiration - don't guess, fall through to relative-phrase parsing
        # (which will also likely find nothing, correctly yielding a miss).

    return resolve_relative_expiration(text_lower, agreement_anchor, effective_anchor)


def normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def text_match(predicted: str, gold: str) -> bool:
    norm_pred, norm_gold = normalize_text(predicted), normalize_text(gold)
    if not norm_pred or not norm_gold:
        return False
    # Word-boundary containment, not raw substring containment: a short gold
    # token (e.g. the abbreviation "MA") must appear as its own word, not
    # merely as letters embedded inside an unrelated longer word (e.g. "MA"
    # inside "Marketing"). This was overly permissive before - a full
    # party-recital sentence could substring-match a terse CUAD gold answer
    # by coincidence rather than genuine content overlap.
    if re.search(rf"\b{re.escape(norm_gold)}\b", norm_pred) or re.search(rf"\b{re.escape(norm_pred)}\b", norm_gold):
        return True
    pred_tokens = set(norm_pred.split())
    gold_tokens = set(norm_gold.split())
    if not pred_tokens or not gold_tokens:
        return False
    overlap = len(pred_tokens & gold_tokens) / len(gold_tokens)
    return overlap >= 0.6


def parse_ground_truth_list(raw: str):
    """extraction_benchmark.csv encodes list-valued answers as Python list literals."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        value = ast.literal_eval(raw)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [str(value).strip()] if str(value).strip() else []
    except (ValueError, SyntaxError):
        return [raw]


def load_benchmark_rows():
    with open(BENCHMARK_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_checkpoint(path: str):
    """
    Return {filename: {"filename": ..., "results": ..., "extracted": [...]}}
    for contracts already evaluated in a prior (possibly interrupted) run,
    so a run can resume across multiple invocations - e.g. across days, if a
    free-tier daily quota is exhausted mid-run - without re-spending API
    calls on contracts already scored.

    "extracted" (added alongside "results") holds the raw predicted clauses
    for ALL 41 types, not just whichever ones were scored at checkpoint time -
    so if a later run adds a new scoring dimension (e.g. the risk-relevant
    categories), already-checkpointed contracts can be RE-scored against it
    for free, without a new LLM call. Older checkpoint files predating this
    field simply won't have it - callers fall back to whatever "results" was
    already scored for those entries.
    """
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    done[entry["filename"]] = entry
    return done


def append_checkpoint(path: str, entry: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


# --- Stratified sampling --------------------------------------------------
#
# Document Name and Parties are ~100% present across the corpus and already
# scored at ~1.0 F1 on the full 497-contract Flash-Lite run - not worth
# spending a scarce 20/day production-model quota re-confirming them. This
# sampler instead weights contract selection toward combinations of
# Agreement/Effective/Expiration Date presence, so a small (~90-contract)
# sample still yields a meaningful number of TP/FN/FP data points for the
# three types that scored weakest. Document Name/Parties are still scored
# incidentally on every sampled contract (free signal), just not targeted.
#
# Fractions were chosen from the actual corpus's joint presence rates (see
# the "All 3 present" bucket dominating at 250/497) to concentrate sampling
# where it's most informative while still covering every presence/absence
# combination at least a little (so precision - i.e. false positives on an
# absent type - stays measurable too).
STRATA_FRACTIONS = {
    (True, True, True): 0.50,     # Agreement + Effective + Expiration all present
    (True, True, False): 0.16,    # Expiration absent - precision check
    (True, False, True): 0.11,    # Effective absent
    (True, False, False): 0.11,   # Effective + Expiration both absent
    (False, True, True): 0.06,    # Agreement absent
    (False, False, True): 0.03,   # only Expiration present
    (False, False, False): 0.03,  # none present - negative control
}


def _bucket_key(row):
    def present(col):
        return bool(row.get(col, "").strip())
    return (present("Agreement Date-Answer"), present("Effective Date-Answer"), present("Expiration Date-Answer"))


def build_stratified_sample(rows, corpus, target_size, seed=42):
    """
    Select `target_size` contracts weighted toward Agreement/Effective/
    Expiration Date presence combinations per STRATA_FRACTIONS, restricted to
    contracts with cached full text. Deterministic given the same rows/corpus
    and seed - callers should still persist the resulting filename list
    (see stratified_sample_path) so a multi-day run targets the exact same
    set on every invocation regardless of when/how often this is called.
    """
    import random
    rng = random.Random(seed)

    def has_text(row):
        fn = row["Filename"]
        title = fn[:-4] if fn.lower().endswith(".pdf") else fn
        return title in corpus

    available = [r for r in rows if has_text(r)]
    buckets = defaultdict(list)
    for r in available:
        buckets[_bucket_key(r)].append(r)
    for b in buckets.values():
        rng.shuffle(b)

    counts = {k: min(round(frac * target_size), len(buckets.get(k, []))) for k, frac in STRATA_FRACTIONS.items()}
    # Reconcile rounding against the actual target: adjust the largest
    # (best-stocked) bucket, "all three present", to hit target_size exactly.
    shortfall = target_size - sum(counts.values())
    all_three = (True, True, True)
    counts[all_three] = max(0, min(counts[all_three] + shortfall, len(buckets.get(all_three, []))))

    selected = []
    for key, n in counts.items():
        selected.extend(buckets.get(key, [])[:n])

    rng.shuffle(selected)  # mix strata together rather than one bucket at a time
    return selected


def load_or_build_stratified_sample(rows, corpus, target_size, seed, run_name):
    path = stratified_sample_path(run_name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            filenames = json.load(f)
        by_filename = {r["Filename"]: r for r in rows}
        return [by_filename[fn] for fn in filenames if fn in by_filename]

    sample = build_stratified_sample(rows, corpus, target_size, seed)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([r["Filename"] for r in sample], f, indent=2)
    return sample


def _first_parsed_date(texts):
    for t in texts:
        d = parse_date_loose(t)
        if d:
            return d
    return None


def evaluate_contract(column_gold: dict, predicted_by_type: dict, risk_gold: dict = None) -> dict:
    """
    Score one contract's 5 metadata columns plus (if risk_gold is given) the
    36 risk-relevant categories; returns {column_or_category: {gold,
    predicted, outcome}}.
    """
    # Anchors for resolving relative Expiration Date phrasing - derived from
    # the model's OWN predictions for this contract, never from ground
    # truth, so scoring doesn't leak gold data into the resolution step.
    agreement_anchor = _first_parsed_date(predicted_by_type.get(CUADClauseType.AGREEMENT_DATE, []))
    effective_anchor = _first_parsed_date(predicted_by_type.get(CUADClauseType.EFFECTIVE_DATE, []))

    results = {}
    for column, clause_type in COLUMN_TO_TYPE.items():
        gold_values = column_gold[column]
        gold_present = len(gold_values) > 0
        preds = predicted_by_type.get(clause_type, [])
        pred_present = len(preds) > 0

        matched = False
        if gold_present and pred_present:
            if column in DATE_COLUMNS:
                gold_dates = {d for d in (parse_date_loose(g) for g in gold_values) if d}
                if column == "Expiration Date-Answer":
                    pred_dates = {
                        d for d in (parse_expiration_date(p, agreement_anchor, effective_anchor) for p in preds) if d
                    }
                else:
                    pred_dates = {d for d in (parse_date_loose(p) for p in preds) if d}
                if gold_dates and pred_dates:
                    matched = bool(gold_dates & pred_dates)
                else:
                    matched = any(text_match(p, g) for p in preds for g in gold_values)
            else:
                matched = any(text_match(p, g) for p in preds for g in gold_values)

        if gold_present and matched:
            outcome = "TP"
        elif gold_present and not matched:
            outcome = "FN"
        elif pred_present and not gold_present:
            outcome = "FP"
        else:
            outcome = "TN"

        results[column] = {"gold": gold_values, "predicted": preds, "outcome": outcome}

    for category, clause_type in RISK_CATEGORY_TYPES.items():
        gold_values = (risk_gold or {}).get(category, [])
        gold_present = len(gold_values) > 0
        preds = predicted_by_type.get(clause_type, [])
        pred_present = len(preds) > 0

        matched = gold_present and pred_present and any(text_match(p, g) for p in preds for g in gold_values)

        if gold_present and matched:
            outcome = "TP"
        elif gold_present and not matched:
            outcome = "FN"
        elif pred_present and not gold_present:
            outcome = "FP"
        else:
            outcome = "TN"

        results[category] = {"gold": gold_values, "predicted": preds, "outcome": outcome}

    return results


def main(limit=None, model=None, sample=None, seed=42, fresh=False, run_name=None, stratified_sample_size=None, rpm_limit=None):
    if not os.path.exists(CORPUS_JSON):
        print(f"ERROR: {CORPUS_JSON} not found. Run prepare_corpus.py first.")
        sys.exit(1)
    with open(CORPUS_JSON, encoding="utf-8") as f:
        corpus = json.load(f)

    from backend.agents.llm_extraction_service import DEFAULT_MODEL
    model = model or DEFAULT_MODEL
    llm = get_default_llm(model=model)
    if llm is None:
        print("ERROR: no GOOGLE_API_KEY (or GEMINI_API_KEY) configured - cannot run extraction.")
        sys.exit(1)
    service = LLMExtractionService(llm)
    if rpm_limit:
        # Paces every real HTTP request the service makes (primary pass AND
        # the conditional fallback pass), not just once per contract - a
        # contract can cost 1 or 2 requests depending on whether
        # FALLBACK_CATEGORIES fires, so pacing at the request level (by
        # wrapping _invoke) is what actually keeps this script under a
        # free-tier requests-per-minute cap.
        limiter = _RpmLimiter(rpm_limit)
        original_invoke = service._invoke

        def _paced_invoke(prompt, source_text, raise_on_error, _orig=original_invoke, _limiter=limiter):
            _limiter.wait()
            return _orig(prompt, source_text, raise_on_error)

        service._invoke = _paced_invoke
        print(f"Rate-limiting to {rpm_limit} requests/minute.")
    print(f"Using model: {model}" + (f"  (run: {run_name})" if run_name else ""))

    risk_ground_truth = {}
    if os.path.exists(RISK_CATEGORIES_JSON):
        with open(RISK_CATEGORIES_JSON, encoding="utf-8") as f:
            risk_ground_truth = json.load(f)
        print(f"Scoring {len(RISK_CATEGORY_TYPES)} risk-relevant categories in addition to the 5 metadata fields "
              f"({RISK_CATEGORIES_JSON}).")
    else:
        print(f"NOTE: {RISK_CATEGORIES_JSON} not found - scoring only the 5 metadata fields. "
              f"Run prepare_risk_category_ground_truth.py to add the 36 risk-relevant categories.")

    ckpt_path = checkpoint_path(run_name)
    res_path = results_path(run_name)

    if fresh and os.path.exists(ckpt_path):
        os.remove(ckpt_path)
    checkpoint = load_checkpoint(ckpt_path)
    if checkpoint:
        print(f"Resuming from checkpoint: {len(checkpoint)} contracts already scored ({ckpt_path}).")

    rows = load_benchmark_rows()
    if stratified_sample_size:
        rows = load_or_build_stratified_sample(rows, corpus, stratified_sample_size, seed, run_name)
        print(f"Stratified sample: {len(rows)} contracts (weighted toward Agreement/Effective/"
              f"Expiration Date presence) - see {stratified_sample_path(run_name)}")
    elif sample:
        import random
        rng = random.Random(seed)
        available = [r for r in rows if corpus.get(
            r["Filename"][:-4] if r["Filename"].lower().endswith(".pdf") else r["Filename"]
        )]
        rows = rng.sample(available, min(sample, len(available)))

    all_scored_types = {**COLUMN_TO_TYPE, **RISK_CATEGORY_TYPES}
    counts = {t: {"tp": 0, "fp": 0, "fn": 0} for t in all_scored_types.values()}
    skipped_no_text = []
    per_contract = []
    evaluated = 0
    new_this_run = 0
    stop_new_attempts = False
    stopped_early = False

    for row in rows:
        filename = row["Filename"]
        title = filename[:-4] if filename.lower().endswith(".pdf") else filename

        if filename in checkpoint:
            entry = checkpoint[filename]
            evaluated += 1

            if "extracted" in entry and risk_ground_truth:
                # Raw predictions were cached - re-score against the full
                # current category set (including risk categories that may
                # not have existed when this contract was first checkpointed)
                # without spending a new LLM call.
                predicted_by_type = defaultdict(list)
                for e in entry["extracted"]:
                    predicted_by_type[CUADClauseType(e["clause_type"])].append(e["extracted_text"])
                column_gold = {column: parse_ground_truth_list(row.get(column, "")) for column in COLUMN_TO_TYPE}
                entry = {**entry, "results": evaluate_contract(column_gold, predicted_by_type, risk_ground_truth.get(title, {}))}

            per_contract.append(entry)
            for column_or_category, clause_type in all_scored_types.items():
                column_result = entry["results"].get(column_or_category)
                if not column_result:
                    continue  # older checkpoint entry never scored this category - not a call worth spending
                outcome = column_result["outcome"]
                if outcome in ("TP", "FP", "FN"):
                    counts[clause_type][outcome.lower()] += 1
            continue

        # A per-run --limit only caps NEW extraction calls (the actual quota
        # spend), never the already-checkpointed contracts above - otherwise
        # a small daily --limit would stop counting long before it even
        # looked at new contracts once the checkpoint alone exceeded it.
        if stop_new_attempts or (limit is not None and new_this_run >= limit):
            continue

        text = corpus.get(title)
        if not text:
            skipped_no_text.append(filename)
            continue

        try:
            extracted = _extract_with_transient_retry(service, text, filename)
        except Exception as e:
            print(f"\nSTOPPING new attempts: extraction failed on {filename}: {e}")
            print("This looks like a quota/rate-limit or network failure rather than a "
                  "per-contract issue - re-run the same command later to resume from the "
                  f"{evaluated} contracts already checkpointed in {ckpt_path}.")
            stopped_early = True
            stop_new_attempts = True
            continue  # keep scanning the rest of the sample to tally checkpoint hits below

        new_this_run += 1
        evaluated += 1
        predicted_by_type = defaultdict(list)
        for e in extracted:
            predicted_by_type[e.clause_type].append(e.extracted_text)

        column_gold = {column: parse_ground_truth_list(row.get(column, "")) for column in COLUMN_TO_TYPE}
        results = evaluate_contract(column_gold, predicted_by_type, risk_ground_truth.get(title, {}))

        for column_or_category, clause_type in all_scored_types.items():
            outcome = results[column_or_category]["outcome"]
            if outcome in ("TP", "FP", "FN"):
                counts[clause_type][outcome.lower()] += 1

        # Store raw predictions (all 41 types), not just the scored subset -
        # so a future new scoring dimension can re-score this contract for
        # free instead of spending another LLM call on it.
        entry = {
            "filename": filename,
            "results": results,
            "extracted": [{"clause_type": e.clause_type.value, "extracted_text": e.extracted_text} for e in extracted],
        }
        append_checkpoint(ckpt_path, entry)
        per_contract.append(entry)
        print(f"[new #{new_this_run}, total {evaluated}/{len(rows)}] {filename}: "
              + ", ".join(f"{c}={r['outcome']}" for c, r in results.items() if c in COLUMN_TO_TYPE))

    report = {}
    for clause_type, c in counts.items():
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        report[clause_type.value] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    remaining = len(rows) - evaluated - len(skipped_no_text)
    output = {
        "model": model,
        "run_name": run_name,
        "sample_size": len(rows),
        "contracts_evaluated": evaluated,
        "newly_evaluated_this_run": new_this_run,
        "contracts_remaining": remaining,
        "contracts_skipped_no_text": len(skipped_no_text),
        "skipped_filenames": skipped_no_text,
        "stopped_early": stopped_early,
        "per_type": report,
        "per_contract": per_contract,
    }
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("\n=== Per-type Precision / Recall / F1 (cumulative, all checkpointed contracts) ===")
    for clause_type, m in report.items():
        print(f"{clause_type:35s} P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}  "
              f"(tp={m['tp']} fp={m['fp']} fn={m['fn']})")
    print(f"\nSample progress: {evaluated}/{len(rows)} done ({new_this_run} newly evaluated this run), "
          f"{remaining} remaining ({len(skipped_no_text)} skipped - no cached text)."
          + (" Run stopped early - see message above." if stopped_early else ""))
    print(f"Full results (including per-contract detail): {res_path}")
    print(f"Checkpoint (resumable progress): {ckpt_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Cap NEW extraction calls this invocation (e.g. --limit 20 for a free-tier "
                              "daily quota) - already-checkpointed contracts are always fully tallied "
                              "regardless of this limit. Re-run the same command later to continue.")
    parser.add_argument("--model", type=str, default=None, help="Override the Gemini model (default: production model, gemini-2.5-flash)")
    parser.add_argument("--sample", type=int, default=None, help="Randomly sample N contracts (fixed seed) instead of taking them in CSV order")
    parser.add_argument("--stratified-sample", type=int, default=None, dest="stratified_sample_size",
                         help="Select N contracts weighted toward Agreement/Effective/Expiration Date "
                              "presence (see STRATA_FRACTIONS). Persisted per --run-name so repeated "
                              "invocations target the same set.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for --sample / --stratified-sample (default: 42)")
    parser.add_argument("--fresh", action="store_true", help="Discard any existing checkpoint (and stratified sample selection) and start over")
    parser.add_argument("--run-name", type=str, default=None,
                         help="Suffix for checkpoint/results/stratified-sample files (e.g. "
                              "gemini-2.5-flash-stratified), so this run doesn't collide with another "
                              "model's or sample's files. Omit to use the plain default filenames.")
    parser.add_argument("--rpm-limit", type=int, default=None, dest="rpm_limit",
                         help="Cap real HTTP requests/minute (paces both the primary and any fallback "
                              "call). Use when a model's free-tier per-minute cap is lower than this "
                              "script's natural request rate (observed: gemini-flash-lite-latest at 15 "
                              "RPM) - unset means no pacing.")
    args = parser.parse_args()
    main(limit=args.limit, model=args.model, sample=args.sample, seed=args.seed, fresh=args.fresh,
         run_name=args.run_name, stratified_sample_size=args.stratified_sample_size, rpm_limit=args.rpm_limit)
