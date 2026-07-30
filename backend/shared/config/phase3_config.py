import os
from typing import Dict, Any

class Phase3Config:
    """Configuration for Phase 3 optimizations"""
    
    # Cache Configuration
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
    CACHE_DEFAULT_TTL = int(os.getenv("CACHE_DEFAULT_TTL", "3600"))  # 1 hour
    
    # Cache TTL by operation type
    CACHE_TTL_CONFIG = {
        "deviation_analysis": int(os.getenv("CACHE_TTL_DEVIATION", "1800")),  # 30 minutes
        "jurisdiction_analysis": int(os.getenv("CACHE_TTL_JURISDICTION", "3600")),  # 1 hour
        "precedent_clause": int(os.getenv("CACHE_TTL_PRECEDENT", "7200")),  # 2 hours
        # Keyed on content hash (contract/clause text + prompt version), not
        # contract_id/time - correctness depends only on what was asked, not
        # when. A long-but-bounded TTL avoids unbounded growth while still
        # capturing the common "re-analyze the same contract" case.
        "clause_extraction": int(os.getenv("CACHE_TTL_CLAUSE_EXTRACTION", "86400")),  # 24 hours
        "policy_evaluation": int(os.getenv("CACHE_TTL_POLICY_EVALUATION", "86400")),  # 24 hours
    }
    
    # Performance Monitoring
    MONITORING_ENABLED = os.getenv("MONITORING_ENABLED", "true").lower() == "true"
    PERFORMANCE_ALERT_THRESHOLDS = {
        "cuad_analysis": int(os.getenv("ALERT_THRESHOLD_CUAD", "5000")),  # 5 seconds
        "deviation_detection": int(os.getenv("ALERT_THRESHOLD_DEVIATION", "2000")),  # 2 seconds
        "precedent_matching": int(os.getenv("ALERT_THRESHOLD_PRECEDENT", "3000")),  # 3 seconds
        "jurisdiction_adaptation": int(os.getenv("ALERT_THRESHOLD_JURISDICTION", "1000")),  # 1 second
    }
    
    # Parallel Processing
    MAX_WORKERS_DEVIATION = int(os.getenv("MAX_WORKERS_DEVIATION", "3"))
    MAX_WORKERS_PRECEDENT = int(os.getenv("MAX_WORKERS_PRECEDENT", "5"))
    MAX_WORKERS_BATCH = int(os.getenv("MAX_WORKERS_BATCH", "5"))
    
    # Timeout Configuration
    TIMEOUT_PATTERN_MATCHING = float(os.getenv("TIMEOUT_PATTERN_MATCHING", "1.0"))  # 1 second
    TIMEOUT_PRECEDENT_CLAUSE = float(os.getenv("TIMEOUT_PRECEDENT_CLAUSE", "5.0"))  # 5 seconds
    TIMEOUT_BATCH_CONTRACT = float(os.getenv("TIMEOUT_BATCH_CONTRACT", "30.0"))  # 30 seconds
    
    # Batch Processing
    MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "50"))
    BACKGROUND_BATCH_THRESHOLD = int(os.getenv("BACKGROUND_BATCH_THRESHOLD", "10"))
    BATCH_SEMAPHORE_LIMIT = int(os.getenv("BATCH_SEMAPHORE_LIMIT", "5"))
    
    # Feature Flags
    ENABLE_SEMANTIC_ANALYSIS = os.getenv("ENABLE_SEMANTIC_ANALYSIS", "true").lower() == "true"
    ENABLE_PARALLEL_PROCESSING = os.getenv("ENABLE_PARALLEL_PROCESSING", "true").lower() == "true"
    ENABLE_ADAPTIVE_LEARNING = os.getenv("ENABLE_ADAPTIVE_LEARNING", "true").lower() == "true"
    
    # Fallback Configuration
    ENABLE_PHASE_FALLBACK = os.getenv("ENABLE_PHASE_FALLBACK", "true").lower() == "true"
    FALLBACK_ORDER = ["phase3", "phase2", "phase1"]
    
    @classmethod
    def get_cache_ttl(cls, operation: str) -> int:
        """Get cache TTL for specific operation"""
        return cls.CACHE_TTL_CONFIG.get(operation, cls.CACHE_DEFAULT_TTL)
    
    @classmethod
    def get_alert_threshold(cls, operation: str) -> int:
        """Get performance alert threshold for operation"""
        return cls.PERFORMANCE_ALERT_THRESHOLDS.get(operation, 5000)
    
    @classmethod
    def is_feature_enabled(cls, feature: str) -> bool:
        """Check if specific feature is enabled"""
        feature_flags = {
            "semantic_analysis": cls.ENABLE_SEMANTIC_ANALYSIS,
            "parallel_processing": cls.ENABLE_PARALLEL_PROCESSING,
            "adaptive_learning": cls.ENABLE_ADAPTIVE_LEARNING,
            "cache": cls.CACHE_ENABLED,
            "monitoring": cls.MONITORING_ENABLED,
            "fallback": cls.ENABLE_PHASE_FALLBACK,
        }
        return feature_flags.get(feature, False)
    
    @classmethod
    def get_config_summary(cls) -> Dict[str, Any]:
        """Get configuration summary for monitoring"""
        return {
            "cache": {
                "enabled": cls.CACHE_ENABLED,
                "redis_url": cls.REDIS_URL,
                "default_ttl": cls.CACHE_DEFAULT_TTL,
                "operation_ttls": cls.CACHE_TTL_CONFIG
            },
            "monitoring": {
                "enabled": cls.MONITORING_ENABLED,
                "alert_thresholds": cls.PERFORMANCE_ALERT_THRESHOLDS
            },
            "parallel_processing": {
                "deviation_workers": cls.MAX_WORKERS_DEVIATION,
                "precedent_workers": cls.MAX_WORKERS_PRECEDENT,
                "batch_workers": cls.MAX_WORKERS_BATCH
            },
            "timeouts": {
                "pattern_matching": cls.TIMEOUT_PATTERN_MATCHING,
                "precedent_clause": cls.TIMEOUT_PRECEDENT_CLAUSE,
                "batch_contract": cls.TIMEOUT_BATCH_CONTRACT
            },
            "batch_processing": {
                "max_batch_size": cls.MAX_BATCH_SIZE,
                "background_threshold": cls.BACKGROUND_BATCH_THRESHOLD,
                "semaphore_limit": cls.BATCH_SEMAPHORE_LIMIT
            },
            "features": {
                "semantic_analysis": cls.ENABLE_SEMANTIC_ANALYSIS,
                "parallel_processing": cls.ENABLE_PARALLEL_PROCESSING,
                "adaptive_learning": cls.ENABLE_ADAPTIVE_LEARNING,
                "phase_fallback": cls.ENABLE_PHASE_FALLBACK
            }
        }