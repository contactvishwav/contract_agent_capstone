"""Schema support for encrypted original PDFs and page provenance."""

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class PdfSourceSchemaMigration:
    def __init__(self, graph=None):
        self.graph = graph or Neo4jContractRepository().graph

    def migrate(self):
        queries = [
            "CREATE CONSTRAINT source_page_id_unique IF NOT EXISTS FOR (p:SourcePage) REQUIRE p.page_id IS UNIQUE",
            "CREATE INDEX source_page_tenant_contract IF NOT EXISTS FOR (p:SourcePage) ON (p.tenant_id, p.contract_id)",
            "CREATE INDEX source_page_tenant_contract_page IF NOT EXISTS FOR (p:SourcePage) ON (p.tenant_id, p.contract_id, p.page_number)",
        ]
        for query in queries:
            self.graph.query(query)
        logger.info("PDF source/page provenance schema migration completed")


if __name__ == "__main__":
    PdfSourceSchemaMigration().migrate()
