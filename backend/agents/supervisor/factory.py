from .supervisor_agent import SupervisorAgent
from .agent_registry import AgentRegistry
from .quality_manager import QualityManager

class SupervisorFactory:
    @staticmethod
    def create_supervisor() -> SupervisorAgent:
        """Create supervisor with registered agents"""
        registry = AgentRegistry()

        # TODO: Register existing agents with adapters
        # registry.register_agent("pdf-processing", PDFProcessingAgentAdapter())
        # registry.register_agent("clause-extraction", ClauseExtractionAgentAdapter())

        quality_manager = QualityManager()
        return SupervisorAgent(registry, quality_manager)