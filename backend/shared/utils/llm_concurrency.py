"""
Shared concurrency limiter for outbound Gemini calls (clause extraction,
policy evaluation). Same conceptual pattern already used in this codebase
(asyncio.Semaphore in optimized_cuad_tools.py's BatchProcessor and
embedding_optimizer.py's BatchProcessor) - but those construct a fresh
semaphore per call site and guard unrelated batch-processing endpoints, not
the actual LLM extraction/evaluation call path (see docs/ENTERPRISE_
READINESS.md's rate-limiting finding).

LLMExtractionService.extract_clauses and PolicyEvaluationService.
evaluate_clause are plain synchronous methods (not `async def`), so
asyncio.Semaphore doesn't apply directly there - threading.Semaphore is the
correct primitive for a blocking call shared across threads/requests, and
is used here as one shared, module-level instance (not one per call site),
so the concurrency limit is actually process-wide.
"""

import os
import threading

LLM_MAX_CONCURRENT_CALLS = int(os.getenv("LLM_MAX_CONCURRENT_CALLS", "5"))

llm_call_semaphore = threading.Semaphore(LLM_MAX_CONCURRENT_CALLS)
