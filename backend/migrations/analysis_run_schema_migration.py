"""Schema support for immutable, replayable contract analysis runs."""

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class AnalysisRunSchemaMigration:
    def __init__(self, graph=None):
        self.graph = graph or Neo4jContractRepository().graph

    def migrate(self):
        queries = [
            "CREATE CONSTRAINT analysis_run_id_unique IF NOT EXISTS FOR (a:AnalysisRun) REQUIRE a.analysis_id IS UNIQUE",
            "CREATE INDEX analysis_run_tenant_contract IF NOT EXISTS FOR (a:AnalysisRun) ON (a.tenant_id, a.contract_id)",
            "CREATE INDEX analysis_run_tenant_created IF NOT EXISTS FOR (a:AnalysisRun) ON (a.tenant_id, a.created_at)",
        ]
        for query in queries:
            self.graph.query(query)
        logger.info("AnalysisRun schema migration completed")


if __name__ == "__main__":
    AnalysisRunSchemaMigration().migrate()
