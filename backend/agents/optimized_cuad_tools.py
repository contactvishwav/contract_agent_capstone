from backend.agents.enhanced_cuad_tools import (
    EnhancedDeviationDetectorTool, 
    EnhancedJurisdictionAdapterTool, 
    EnhancedPrecedentMatcherTool
)
from backend.shared.cache.redis_cache import cache_result
from backend.shared.monitoring.performance_monitor import track_performance
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class OptimizedDeviationDetectorTool(EnhancedDeviationDetectorTool):
    """Optimized deviation detection with caching and performance monitoring"""
    
    name: str = "optimized_deviation_detector"
    description: str = "High-performance deviation detection with caching and monitoring"
    
    @track_performance("deviation_detection")
    @cache_result("deviation_analysis", ttl=1800)  # 30 minutes cache
    def _run(self, clauses_json: str, tenant_id: str) -> str:
        """Cached and monitored deviation detection"""
        return super()._run(clauses_json)
    
    def _semantic_deviation_analysis(self, clause: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Optimized semantic analysis with parallel processing"""
        content = clause.get("content", "").lower()
        clause_type = clause.get("clause_type", "").lower()
        
        # Parallel pattern matching for better performance
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            
            for pattern_name, pattern_config in self.semantic_patterns.items():
                future = executor.submit(
                    self._check_pattern_match, 
                    content, clause_type, pattern_name, pattern_config, clause
                )
                futures.append(future)
            
            # Collect results
            deviations = []
            for future in futures:
                try:
                    result = future.result(timeout=1.0)  # 1 second timeout per pattern
                    if result:
                        deviations.append(result)
                except Exception as e:
                    logger.warning(f"Pattern matching failed: {e}")
            
            return deviations
    
    def _check_pattern_match(self, content: str, clause_type: str, pattern_name: str, 
                           pattern_config: Dict[str, Any], clause: Dict[str, Any]) -> Dict[str, Any]:
        """Check individual pattern match"""
        if self._matches_semantic_pattern(content, clause_type, pattern_config):
            return {
                "clause_type": clause.get("clause_type", ""),
                "deviation_type": pattern_name,
                "severity": pattern_config["severity"],
                "issue": pattern_config["issue"],
                "suggested_fix": pattern_config["fix"],
                "clause_content": clause.get("content", "")[:200] + "..." if len(clause.get("content", "")) > 200 else clause.get("content", ""),
                "detection_method": "semantic_analysis",
                "confidence_score": pattern_config.get("confidence", 0.8)
            }
        return None

class OptimizedJurisdictionAdapterTool(EnhancedJurisdictionAdapterTool):
    """Optimized jurisdiction adaptation with caching"""
    
    name: str = "optimized_jurisdiction_adapter"
    description: str = "High-performance jurisdiction adaptation with caching"
    
    @track_performance("jurisdiction_adaptation")
    @cache_result("jurisdiction_analysis", ttl=3600)  # 1 hour cache
    def _run(self, contract_text: str, industry: str = None) -> str:
        """Cached and monitored jurisdiction adaptation"""
        return super()._run(contract_text, industry)

class OptimizedPrecedentMatcherTool(EnhancedPrecedentMatcherTool):
    """Optimized precedent matching with advanced caching"""
    
    name: str = "optimized_precedent_matcher"
    description: str = "High-performance precedent matching with intelligent caching"
    
    @track_performance("precedent_matching")
    def _run(self, clauses_json: str, tenant_id: str) -> str:
        """Optimized precedent matching with clause-level caching"""
        if not tenant_id:
            raise ValueError("Authenticated tenant_id is required for precedent matching")
        try:
            clauses = json.loads(clauses_json)
            matches = []
            
            # Process clauses in parallel for better performance
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(self._process_clause_precedents, clause, tenant_id): clause
                    for clause in clauses
                }
                
                for future in futures:
                    try:
                        result = future.result(timeout=5.0)  # 5 second timeout per clause
                        if result:
                            matches.append(result)
                    except Exception as e:
                        logger.warning(f"Precedent matching failed for clause: {e}")
            
            return json.dumps(matches)
            
        except Exception as e:
            logger.error(f"Optimized precedent matching failed: {e}")
            return json.dumps([])
    
    @cache_result("precedent_clause", ttl=7200)  # 2 hour cache per clause
    def _process_clause_precedents(self, clause: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        """Process precedents for individual clause with caching"""
        # Get real precedents from database
        real_precedents = self._find_real_precedents(clause, tenant_id)
        
        if real_precedents:
            analysis = self._analyze_precedents(real_precedents, clause)
            return {
                "clause": clause,
                "precedent_count": len(real_precedents),
                "approval_rate": analysis["approval_rate"],
                "risk_patterns": analysis["risk_patterns"],
                "recommendations": analysis["recommendations"],
                "similar_contracts": analysis["similar_contracts"],
                "trend_analysis": analysis["trend_analysis"]
            }
        else:
            # Fallback to mock data
            fallback_precedents = self._find_similar_clauses(clause)
            if fallback_precedents:
                return {
                    "clause": clause,
                    "precedent_count": len(fallback_precedents),
                    "approval_rate": self._calculate_approval_rate(fallback_precedents),
                    "risk_patterns": self._identify_risk_patterns(fallback_precedents),
                    "recommendations": self._generate_recommendations(fallback_precedents),
                    "similar_contracts": [],
                    "trend_analysis": {"note": "Limited historical data available"}
                }
        
        return None

class BatchProcessor:
    """Batch processing for multiple contracts"""
    
    def __init__(self):
        self.deviation_tool = OptimizedDeviationDetectorTool()
        self.jurisdiction_tool = OptimizedJurisdictionAdapterTool()
        self.precedent_tool = OptimizedPrecedentMatcherTool()
    
    @track_performance("batch_processing")
    async def process_contracts_batch(self, contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process multiple contracts in parallel"""
        semaphore = asyncio.Semaphore(5)  # Limit concurrent processing
        
        async def process_single_contract(contract):
            async with semaphore:
                return await self._process_contract_async(contract)
        
        # Process all contracts concurrently
        tasks = [process_single_contract(contract) for contract in contracts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions and return successful results
        successful_results = [
            result for result in results 
            if not isinstance(result, Exception)
        ]
        
        return successful_results
    
    async def _process_contract_async(self, contract: Dict[str, Any]) -> Dict[str, Any]:
        """Process single contract asynchronously"""
        try:
            contract_text = contract.get("text", "")
            clauses = contract.get("clauses", [])
            tenant_id = contract.get("tenant_id")
            if not tenant_id:
                raise ValueError("Authenticated tenant_id is required for optimized CUAD processing")
            
            # Run analysis in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            
            with ThreadPoolExecutor(max_workers=3) as executor:
                # Submit all tasks
                deviation_future = loop.run_in_executor(
                    executor, self.deviation_tool._run, json.dumps(clauses), tenant_id
                )
                jurisdiction_future = loop.run_in_executor(
                    executor, self.jurisdiction_tool._run, contract_text
                )
                precedent_future = loop.run_in_executor(
                    executor,
                    self.precedent_tool._run,
                    json.dumps(clauses),
                    tenant_id,
                )
                
                # Wait for all results
                deviations_json, jurisdiction_json, precedents_json = await asyncio.gather(
                    deviation_future, jurisdiction_future, precedent_future
                )
            
            return {
                "contract_id": contract.get("id"),
                "deviations": json.loads(deviations_json),
                "jurisdiction": json.loads(jurisdiction_json),
                "precedents": json.loads(precedents_json),
                "processing_time": contract.get("processing_time", 0)
            }
            
        except Exception as e:
            logger.error(f"Contract processing failed: {e}")
            return {
                "contract_id": contract.get("id"),
                "error": str(e),
                "deviations": [],
                "jurisdiction": {},
                "precedents": []
            }
