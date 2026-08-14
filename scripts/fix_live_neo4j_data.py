import os
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.shared.utils.contract_search_tool import graph

def fix_neo4j_filenames():
    print("=== FIXING NEO4J FILENAMES & DOCUMENT METADATA ===")
    
    # 1. Update Contract filenames based on source_filename or ID matching
    cypher_contracts = """
    MATCH (c:Contract)
    WHERE c.filename IS NULL OR c.filename STARTS WITH 'UPLOADED_' OR c.filename = ''
    SET c.filename = CASE
        WHEN c.source_filename IS NOT NULL AND c.source_filename <> '' THEN c.source_filename
        WHEN c.file_id CONTAINS '223C84D9' THEN 'Salesforce_MSA.pdf'
        WHEN c.file_id CONTAINS '0325217C' THEN 'Contract_Policy_Playbook.pdf'
        WHEN c.file_id CONTAINS 'SHELL' OR c.file_id CONTAINS 'MESA' THEN 'Shell_Pacific_Corp_MESA.pdf'
        WHEN c.file_id CONTAINS 'SOW' THEN 'Clean_SOW.pdf'
        WHEN c.file_id CONTAINS 'MSA' THEN 'Clean_MSA.pdf'
        ELSE c.filename
    END
    RETURN c.file_id AS file_id, c.filename AS filename
    """
    results = graph.query(cypher_contracts)
    print(f"Updated Contract nodes: {len(results)}")
    for r in results:
        print(f"  Contract: {r['file_id']} -> filename: {r['filename']}")

    # 2. Propagate filename and contract_id onto Document nodes
    cypher_documents = """
    MATCH (c:Contract)
    OPTIONAL MATCH (d:Document) WHERE d.id = c.file_id OR d.contract_id = c.file_id
    WITH c, d WHERE d IS NOT NULL
    SET d.filename = c.filename,
        d.contract_id = c.file_id
    RETURN d.id AS doc_id, d.filename AS filename
    """
    doc_results = graph.query(cypher_documents)
    print(f"Updated Document nodes: {len(doc_results)}")

    # 3. Propagate filename onto Section, Clause, and Chunk nodes
    cypher_sections = """
    MATCH (c:Contract)-[:HAS_SECTION]->(s:Section)
    SET s.filename = c.filename
    RETURN count(s) AS section_count
    """
    sec_count = graph.query(cypher_sections)
    print(f"Updated Section nodes: {sec_count[0]['section_count'] if sec_count else 0}")

    cypher_clauses = """
    MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl:Clause)
    SET cl.filename = c.filename
    RETURN count(cl) AS clause_count
    """
    cl_count = graph.query(cypher_clauses)
    print(f"Updated Clause nodes: {cl_count[0]['clause_count'] if cl_count else 0}")

    cypher_chunks = """
    MATCH (c:Contract)-[:HAS_CHUNK|HAS_SOURCE_PAGE*1..2]->(chk)
    SET chk.filename = c.filename
    RETURN count(chk) AS chunk_count
    """
    chk_count = graph.query(cypher_chunks)
    print(f"Updated Chunk nodes: {chk_count[0]['chunk_count'] if chk_count else 0}")

    print("=== NEO4J FILENAMES REPAIR COMPLETE ===")

if __name__ == "__main__":
    fix_neo4j_filenames()
