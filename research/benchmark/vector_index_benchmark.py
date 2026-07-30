"""
Before/after latency benchmark for P3 item 16: brute-force vector.similarity.
cosine() scans (MATCH + per-row cosine calc across every Contract node)
replaced by a native Neo4j vector index (db.index.vector.queryNodes, see
backend/migrations/vector_index_migration.py and backend/shared/utils/
vector_index_config.py).

Seeds N synthetic Contract nodes with random EMBEDDING_DIMENSIONS-length
vectors (semantic content doesn't matter for a query-latency benchmark -
only vector count/dimensionality affects brute-force scan cost) across a
handful of tenants, times both query patterns over the same set of random
query vectors, reports average latency and the speedup factor, then deletes
everything it created.

Requires a reachable Neo4j (NEO4J_URI/USERNAME/PASSWORD/DATABASE env vars)
with the vector_indexes migration already applied.
"""

import os
import random
import statistics
import time

from langchain_neo4j import Neo4jGraph

from backend.shared.utils.vector_index_config import CONTRACT_EMBEDDING_INDEX, EMBEDDING_DIMENSIONS

N_CONTRACTS = int(os.getenv("BENCHMARK_N_CONTRACTS", "5000"))
N_QUERIES = int(os.getenv("BENCHMARK_N_QUERIES", "20"))
TENANTS = ["bench_tenant_a", "bench_tenant_b", "bench_tenant_c"]
BENCH_MARKER = "vector_index_benchmark_seed"


def random_embedding():
    return [random.uniform(-1, 1) for _ in range(EMBEDDING_DIMENSIONS)]


def seed(graph: Neo4jGraph):
    print(f"Seeding {N_CONTRACTS} synthetic Contract nodes...")
    batch_size = 500
    for start in range(0, N_CONTRACTS, batch_size):
        batch = [
            {
                "file_id": f"bench_{i}",
                "tenant_id": random.choice(TENANTS),
                "embedding": random_embedding(),
            }
            for i in range(start, min(start + batch_size, N_CONTRACTS))
        ]
        graph.query(
            """
            UNWIND $batch AS row
            CREATE (c:Contract {
                file_id: row.file_id, tenant_id: row.tenant_id,
                embedding: row.embedding, _bench_marker: $marker
            })
            """,
            {"batch": batch, "marker": BENCH_MARKER},
        )
    print("Seeding complete.")


def cleanup(graph: Neo4jGraph):
    graph.query("MATCH (c:Contract {_bench_marker: $marker}) DETACH DELETE c", {"marker": BENCH_MARKER})
    print("Cleaned up synthetic benchmark data.")


def time_query(graph: Neo4jGraph, cypher: str, param_fn, n=N_QUERIES):
    durations = []
    for _ in range(n):
        params = param_fn()
        start = time.perf_counter()
        graph.query(cypher, params)
        durations.append((time.perf_counter() - start) * 1000)
    return durations


def brute_force_params():
    return {
        "query_embedding": random_embedding(),
        "tenant_id": random.choice(TENANTS),
    }


def indexed_params():
    return {
        "query_embedding": random_embedding(),
        "tenant_id": random.choice(TENANTS),
        "k": 200,
    }


BRUTE_FORCE_CYPHER = """
MATCH (c:Contract {_bench_marker: $marker})
WHERE c.tenant_id = $tenant_id
WITH c, vector.similarity.cosine(c.embedding, $query_embedding) AS score
WHERE score > 0.3
RETURN c.file_id AS file_id, score
ORDER BY score DESC
LIMIT 10
"""

INDEXED_CYPHER = f"""
CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $query_embedding)
YIELD node AS c, score
WHERE c.tenant_id = $tenant_id AND c._bench_marker = $marker
RETURN c.file_id AS file_id, score
ORDER BY score DESC
LIMIT 10
"""


def report(label, durations):
    print(f"\n{label} (n={len(durations)} queries):")
    print(f"  mean:   {statistics.mean(durations):.2f}ms")
    print(f"  median: {statistics.median(durations):.2f}ms")
    print(f"  p95:    {sorted(durations)[int(len(durations) * 0.95)]:.2f}ms")
    print(f"  min/max: {min(durations):.2f}ms / {max(durations):.2f}ms")


def main():
    graph = Neo4jGraph(
        url=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
        # This bare community-edition container has no APOC plugin
        # installed, which Neo4jGraph's default schema refresh requires
        # (apoc.meta.data()) - not needed for this benchmark anyway.
        refresh_schema=False,
    )

    existing = graph.query("MATCH (c:Contract {_bench_marker: $marker}) RETURN count(c) AS n", {"marker": BENCH_MARKER})
    if existing[0]["n"] == 0:
        seed(graph)
    else:
        print(f"Reusing {existing[0]['n']} already-seeded benchmark nodes.")

    try:
        brute_force_durations = time_query(
            graph,
            BRUTE_FORCE_CYPHER,
            lambda: {**brute_force_params(), "marker": BENCH_MARKER},
        )
        # Let the vector index's background populate finish before querying it -
        # CREATE VECTOR INDEX populates asynchronously.
        graph.query(f"CALL db.awaitIndex('{CONTRACT_EMBEDDING_INDEX}', 300)")

        indexed_durations = time_query(
            graph,
            INDEXED_CYPHER,
            lambda: {**indexed_params(), "marker": BENCH_MARKER},
        )

        report(f"BEFORE - brute-force vector.similarity.cosine() scan over {N_CONTRACTS} nodes", brute_force_durations)
        report(f"AFTER  - db.index.vector.queryNodes() over {N_CONTRACTS} nodes", indexed_durations)

        speedup = statistics.mean(brute_force_durations) / statistics.mean(indexed_durations)
        print(f"\nSpeedup (mean): {speedup:.1f}x")
    finally:
        cleanup(graph)


if __name__ == "__main__":
    main()
