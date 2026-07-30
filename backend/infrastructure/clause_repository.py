"""
Clause Repository with Order Preservation and CUAD Classification Storage
"""

from typing import List, Dict, Any
import logging

from backend.governance.pii_engine import PIIEngine
from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ClauseRepository:
    """Repository for clause storage with order preservation"""
    
    def __init__(self):
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        self.repository = Neo4jContractRepository()
    
    def store_clauses(self, clauses: List[Dict[str, Any]]) -> bool:
        """Store clauses with guaranteed order preservation"""
        try:
            # Group clauses by section
            clauses_by_section = {}
            for clause in clauses:
                section_id = clause["section_id"]
                if section_id not in clauses_by_section:
                    clauses_by_section[section_id] = []
                clauses_by_section[section_id].append(clause)
            
            # Store clauses for each section
            for section_id, section_clauses in clauses_by_section.items():
                sorted_clauses = sorted(section_clauses, key=lambda x: x['order'])
                
                for clause in sorted_clauses:
                    self._store_single_clause(clause)
            
            logger.info(f"Stored {len(clauses)} clauses across {len(clauses_by_section)} sections")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store clauses: {e}")
            return False
    
    def _store_single_clause(self, clause: Dict[str, Any]):
        """Store individual clause with order preservation"""
        query = """
        MATCH (s:Section {section_id: $section_id})
        CREATE (cl:Clause {
            clause_id: $clause_id,
            content: $content,
            clause_type: $clause_type,
            order: $order,
            start_position: $start_position,
            end_position: $end_position,
            confidence: $confidence,
            section_id: $section_id,
            tenant_id: s.tenant_id,
            created_at: datetime()
        })
        CREATE (s)-[:CONTAINS_CLAUSE {order: $order}]->(cl)
        RETURN cl.clause_id as clause_id
        """

        # Redact-then-encrypt before persistence, matching
        # Neo4jContractRepository.store_contract's treatment of full_text.
        redacted_content = PIIEngine.redact(clause["content"])
        encrypted_content = field_encryptor.encrypt(redacted_content)

        self.repository.graph.query(query, {
            "section_id": clause["section_id"],
            "clause_id": clause["clause_id"],
            "content": encrypted_content,
            "clause_type": clause["clause_type"],
            "order": clause["order"],
            "start_position": clause.get("start_position", 0),
            "end_position": clause.get("end_position", 0),
            "confidence": clause["confidence"]
        })
    
    def store_cuad_classifications(self, classifications: List[Dict[str, Any]]) -> bool:
        """Store CUAD classifications"""
        try:
            for classification in classifications:
                self._store_single_classification(classification)
            
            logger.info(f"Stored {len(classifications)} CUAD classifications")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store CUAD classifications: {e}")
            return False
    
    def _store_single_classification(self, classification: Dict[str, Any]):
        """Store individual CUAD classification"""
        query = """
        MATCH (cl:Clause {clause_id: $clause_id})
        MATCH (ct:ClauseType {cuad_type: $cuad_type})
        CREATE (cl)-[:CLASSIFIED_AS {
            confidence: $confidence,
            detected_by: $detected_by,
            reasoning: $reasoning,
            created_at: datetime()
        }]->(ct)
        """
        
        self.repository.graph.query(query, {
            "clause_id": classification["clause_id"],
            "cuad_type": classification["cuad_type"],
            "confidence": classification["confidence"],
            "detected_by": classification["detected_by"],
            "reasoning": classification.get("reasoning", "")
        })
    
    def get_clauses_ordered(self, section_id: str) -> List[Dict[str, Any]]:
        """Retrieve clauses in correct order for a section"""
        query = """
        MATCH (s:Section {section_id: $section_id})-[r:CONTAINS_CLAUSE]->(cl:Clause)
        OPTIONAL MATCH (cl)-[c:CLASSIFIED_AS]->(ct:ClauseType)
        RETURN cl.clause_id as clause_id,
               cl.content as content,
               cl.clause_type as clause_type,
               cl.order as order,
               cl.confidence as confidence,
               collect({cuad_type: ct.cuad_type, confidence: c.confidence}) as cuad_classifications
        ORDER BY cl.order ASC
        """
        
        result = self.repository.graph.query(query, {"section_id": section_id})
        rows = [dict(row) for row in result]
        for row in rows:
            row["content"] = field_encryptor.decrypt(row.get("content") or "")
        return rows

    def get_clause_count(self, section_id: str) -> int:
        """Get total clause count for section"""
        query = """
        MATCH (s:Section {section_id: $section_id})-[:CONTAINS_CLAUSE]->(cl:Clause)
        RETURN count(cl) as clause_count
        """
        
        result = self.repository.graph.query(query, {"section_id": section_id})
        return result[0]["clause_count"] if result else 0