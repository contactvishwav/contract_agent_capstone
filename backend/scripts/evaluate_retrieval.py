#!/usr/bin/env python3
"""Offline retrieval-quality harness (Phase 5, MLOps governance).

Computes Recall@K and nDCG@K for backend/tests/evals/golden_dataset.json
against a real, running local backend - the same document-level search
endpoint (POST /api/contracts/search/enhanced) Enhanced Search and Contract Chat
use, not a mocked or in-process shortcut. This is a batch job, not a
request-time computation: it authenticates as a fresh tenant (the same
bootstrap flow every Playwright spec uses - see AGENTS.md's "never use
default-tenant" invariant), uploads the standing fixture contracts
(data/*.pdf) if they are not already present for that tenant, runs every
golden query, and writes a timestamped results artifact that
backend/api/admin_evaluations_api.py serves read-only to ADMIN users.

Usage:
    backend/.venv/bin/python backend/scripts/evaluate_retrieval.py
    backend/.venv/bin/python backend/scripts/evaluate_retrieval.py --api-base-url http://localhost:8001

Requires the local Compose stack's backend + neo4j (+ worker not needed -
document-level embeddings are computed synchronously on upload, no
Celery/intelligence-pipeline analysis required for this harness).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DATASET_PATH = REPO_ROOT / "backend" / "tests" / "evals" / "golden_dataset.json"
RESULTS_PATH = REPO_ROOT / "backend" / "tests" / "evals" / "latest_results.json"
FIXTURES_DIR = REPO_ROOT / "data"

DEFAULT_API_BASE_URL = os.environ.get("EVAL_API_BASE_URL", "http://localhost:8001")
UPLOAD_POLL_TIMEOUT_SECONDS = 90
UPLOAD_POLL_INTERVAL_SECONDS = 3


class EvalError(RuntimeError):
    pass


def _bootstrap_tenant(api_base_url: str) -> str:
    """Registers a brand-new tenant/ADMIN user (same bootstrap-only flow
    every Playwright spec uses) and returns a bearer token. A fresh tenant
    per run keeps this script idempotent/re-runnable without ever needing
    to clean up or reuse state, and never touches another tenant's data."""
    suffix = f"{int(time.time())}_{os.getpid()}"
    username = f"eval_harness_{suffix}"
    tenant_id = f"eval_harness_tenant_{suffix}"
    password = "EvalHarnessPassword123!"

    register = requests.post(
        f"{api_base_url}/api/auth/register",
        json={"username": username, "password": password, "tenant_id": tenant_id, "role": "ADMIN"},
        timeout=30,
    )
    if register.status_code != 201:
        raise EvalError(f"Tenant bootstrap failed ({register.status_code}): {register.text}")

    token_resp = requests.post(
        f"{api_base_url}/api/auth/token",
        json={"username": username, "password": password},
        timeout=30,
    )
    if token_resp.status_code != 200:
        raise EvalError(f"Login failed ({token_resp.status_code}): {token_resp.text}")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise EvalError("Login response had no access_token")
    return access_token


def _uploaded_filenames(api_base_url: str, headers: dict[str, str]) -> set[str]:
    resp = requests.get(f"{api_base_url}/api/documents", headers=headers, timeout=30)
    resp.raise_for_status()
    return {row["filename"] for row in resp.json() if row.get("filename")}


def _ingest_fixtures(api_base_url: str, headers: dict[str, str], filenames: list[str]) -> None:
    """Uploads each fixture PDF used by the golden dataset. Document-level
    embeddings are written before /api/documents/upload returns (no
    enable_enhanced=True - clause/section extraction is deliberately out of
    scope, see the golden dataset's own docstring), so a short poll against
    the contract-picker list is enough to confirm each is queryable."""
    for filename in filenames:
        pdf_path = FIXTURES_DIR / filename
        if not pdf_path.exists():
            raise EvalError(f"Fixture PDF not found: {pdf_path}")
        with pdf_path.open("rb") as fh:
            resp = requests.post(
                f"{api_base_url}/api/documents/upload",
                headers=headers,
                files={"file": (filename, fh, "application/pdf")},
                data={"model": "gemini-2.5-flash"},
                timeout=120,
            )
        if resp.status_code != 200:
            raise EvalError(f"Upload of {filename} failed ({resp.status_code}): {resp.text}")

    deadline = time.monotonic() + UPLOAD_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        present = _uploaded_filenames(api_base_url, headers)
        if set(filenames).issubset(present):
            return
        time.sleep(UPLOAD_POLL_INTERVAL_SECONDS)
    missing = set(filenames) - _uploaded_filenames(api_base_url, headers)
    raise EvalError(f"Fixture(s) never became queryable within {UPLOAD_POLL_TIMEOUT_SECONDS}s: {sorted(missing)}")


def _search_document_filenames(api_base_url: str, headers: dict[str, str], query: str, limit: int) -> list[str]:
    resp = requests.post(
        f"{api_base_url}/api/contracts/search/enhanced",
        headers=headers,
        json={"search_level": "document", "query": query},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success", True) or not body.get("results"):
        return []
    documents = body["results"][0].get("documents", [])
    return [doc["filename"] for doc in documents[:limit] if doc.get("filename")]


def _recall_at_k(retrieved: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    hits = len(expected.intersection(retrieved))
    return hits / len(expected)


def _ndcg_at_k(retrieved: list[str], expected: set[str]) -> float:
    """Binary relevance (1 if the retrieved filename is an expected target,
    else 0), standard log2-discounted gain."""
    if not expected:
        return 0.0
    dcg = sum(
        (1.0 if filename in expected else 0.0) / math.log2(rank + 2)
        for rank, filename in enumerate(retrieved)
    )
    ideal_hits = min(len(expected), len(retrieved)) or min(len(expected), 1)
    idcg = sum(1.0 / math.log2(rank + 2) for rank in range(min(len(expected), max(ideal_hits, 1))))
    return dcg / idcg if idcg > 0 else 0.0


def run_evaluation(api_base_url: str) -> dict[str, Any]:
    dataset = json.loads(GOLDEN_DATASET_PATH.read_text())
    k = int(dataset.get("k", 3))
    queries = dataset["queries"]

    token = _bootstrap_tenant(api_base_url)
    headers = {"Authorization": f"Bearer {token}"}

    fixture_filenames = sorted({name for q in queries for name in q["expected_filenames"]})
    _ingest_fixtures(api_base_url, headers, fixture_filenames)

    per_query: list[dict[str, Any]] = []
    for entry in queries:
        expected = set(entry["expected_filenames"])
        retrieved = _search_document_filenames(api_base_url, headers, entry["query"], k)
        recall = _recall_at_k(retrieved, expected)
        ndcg = _ndcg_at_k(retrieved, expected)
        per_query.append(
            {
                "id": entry["id"],
                "query": entry["query"],
                "expected_filenames": sorted(expected),
                "retrieved_filenames": retrieved,
                "recall_at_k": round(recall, 4),
                "ndcg_at_k": round(ndcg, 4),
            }
        )

    mean_recall = sum(r["recall_at_k"] for r in per_query) / len(per_query)
    mean_ndcg = sum(r["ndcg_at_k"] for r in per_query) / len(per_query)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "k": k,
        "search_level": "document",
        "query_count": len(per_query),
        "aggregate": {
            "mean_recall_at_k": round(mean_recall, 4),
            "mean_ndcg_at_k": round(mean_ndcg, 4),
        },
        "per_query": per_query,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--output", default=str(RESULTS_PATH))
    args = parser.parse_args()

    try:
        results = run_evaluation(args.api_base_url)
    except EvalError as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2) + "\n")

    print(f"Recall@{results['k']}: {results['aggregate']['mean_recall_at_k']:.2%}")
    print(f"nDCG@{results['k']}: {results['aggregate']['mean_ndcg_at_k']:.2%}")
    print(f"Wrote {output_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
