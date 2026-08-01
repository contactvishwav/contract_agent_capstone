from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from backend.governance.rbac import Permission, requires_permission
from typing import Dict, List, Any, Optional
import logging
from backend.shared.monitoring.performance_monitor import monitor
from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker
from backend.shared.cache.redis_cache import cache
from backend.shared.utils.contract_search_tool import graph as neo4j_graph
from backend.agents.optimized_cuad_tools import BatchProcessor
import asyncio

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

@router.get("/performance", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_performance_metrics():
    """Get real-time performance metrics"""
    try:
        stats = monitor.get_all_stats()
        
        # Add cache statistics
        cache_stats = {
            "cache_type": "redis" if hasattr(cache.redis_client, 'ping') else "in_memory",
            "cache_status": "connected" if cache.redis_client else "disconnected"
        }
        
        return {
            "performance_metrics": stats,
            "cache_statistics": cache_stats,
            "timestamp": monitor.metrics.get("cuad_analysis", [{}])[-1].timestamp.isoformat() if monitor.metrics.get("cuad_analysis") else None
        }
        
    except Exception as e:
        logger.error(f"Failed to get performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Metrics retrieval failed: {str(e)}")

@router.get("/performance/{operation}", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_operation_performance(operation: str, hours: int = 1):
    """Get performance metrics for specific operation"""
    try:
        stats = monitor.get_stats(operation, hours)
        
        if "error" in stats:
            raise HTTPException(status_code=404, detail=stats["error"])
        
        return {
            "operation": operation,
            "time_window_hours": hours,
            "metrics": stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get operation performance for {operation}: {e}")
        raise HTTPException(status_code=500, detail=f"Operation metrics failed: {str(e)}")

@router.get("/llm-usage", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_llm_usage():
    """Get running LLM token usage, estimated cost, and cache-hit totals (P3 item 20)"""
    try:
        return llm_usage_tracker.get_summary()
    except Exception as e:
        logger.error(f"Failed to get LLM usage summary: {e}")
        raise HTTPException(status_code=500, detail=f"LLM usage retrieval failed: {str(e)}")

@router.get("/health")
async def health_check():
    """System health check.

    Production-readiness audit finding #9: this previously never checked
    Neo4j at all (the platform's single most critical dependency could be
    completely unreachable while this still reported "healthy"), and
    always returned HTTP 200 regardless of the "status" field's value - a
    load balancer, Docker HEALTHCHECK, or orchestrator readiness probe
    polling this route needs the HTTP status itself to reflect health, not
    a body field it would have to parse and interpret. Any non-"healthy"
    status now returns 503 alongside the same body shape as before.
    """
    # Check cache (Redis) connectivity
    cache_healthy = True
    try:
        cache.redis_client.ping()
    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        cache_healthy = False

    # Check Neo4j connectivity - a cheap, real query, not just "did the
    # driver construct" (construction never touches the network).
    neo4j_healthy = True
    try:
        neo4j_graph.query("RETURN 1")
    except Exception as e:
        logger.error(f"Neo4j health check failed: {e}")
        neo4j_healthy = False

    # Check recent performance
    try:
        recent_stats = monitor.get_all_stats()
        performance_healthy = True

        for operation, stats in recent_stats.items():
            if isinstance(stats, dict) and "success_rate" in stats:
                if stats["success_rate"] < 0.9:  # Less than 90% success rate
                    performance_healthy = False
                    break

        metrics_summary = {
            "total_operations": len(recent_stats),
            "avg_success_rate": sum(
                s.get("success_rate", 0) for s in recent_stats.values()
                if isinstance(s, dict)
            ) / max(len(recent_stats), 1)
        }
    except Exception as e:
        logger.error(f"Performance check failed within health check: {e}")
        performance_healthy = False
        metrics_summary = {}

    overall_health = cache_healthy and neo4j_healthy and performance_healthy

    body = {
        "status": "healthy" if overall_health else "degraded",
        "components": {
            "cache": "healthy" if cache_healthy else "unhealthy",
            "neo4j": "healthy" if neo4j_healthy else "unhealthy",
            "performance": "healthy" if performance_healthy else "degraded"
        },
        "metrics_summary": metrics_summary
    }
    return JSONResponse(content=body, status_code=200 if overall_health else 503)

@router.post("/batch-process", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def batch_process_contracts(
    background_tasks: BackgroundTasks,
    contracts: List[Dict[str, Any]]
):
    """Process multiple contracts in batch with monitoring"""
    try:
        if len(contracts) > 50:  # Limit batch size
            raise HTTPException(status_code=400, detail="Batch size limited to 50 contracts")
        
        # Process in background for large batches
        if len(contracts) > 10:
            background_tasks.add_task(process_batch_background, contracts)
            return {
                "status": "processing",
                "message": f"Batch of {len(contracts)} contracts queued for background processing",
                "contract_count": len(contracts)
            }
        else:
            # Process immediately for small batches
            processor = BatchProcessor()
            results = await processor.process_contracts_batch(contracts)
            
            return {
                "status": "completed",
                "results": results,
                "processed_count": len(results),
                "total_count": len(contracts)
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Batch processing failed: {str(e)}")

async def process_batch_background(contracts: List[Dict[str, Any]]):
    """Background batch processing"""
    try:
        processor = BatchProcessor()
        results = await processor.process_contracts_batch(contracts)
        logger.info(f"Background batch processing completed: {len(results)}/{len(contracts)} contracts processed")
    except Exception as e:
        logger.error(f"Background batch processing failed: {e}")

@router.get("/alerts", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_performance_alerts():
    """Get recent performance alerts"""
    try:
        # Get recent metrics that exceeded thresholds
        alerts = []
        
        for operation, metrics_list in monitor.metrics.items():
            threshold = monitor.alert_thresholds.get(operation)
            if threshold:
                recent_alerts = [
                    {
                        "operation": operation,
                        "duration_ms": m.duration_ms,
                        "threshold_ms": threshold,
                        "timestamp": m.timestamp.isoformat(),
                        "severity": "high" if m.duration_ms > threshold * 2 else "medium"
                    }
                    for m in metrics_list[-10:]  # Last 10 metrics
                    if m.duration_ms > threshold
                ]
                alerts.extend(recent_alerts)
        
        # Sort by timestamp (most recent first)
        alerts.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return {
            "alerts": alerts[:20],  # Return last 20 alerts
            "total_alerts": len(alerts),
            "alert_summary": {
                "high_severity": len([a for a in alerts if a["severity"] == "high"]),
                "medium_severity": len([a for a in alerts if a["severity"] == "medium"])
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get performance alerts: {e}")
        raise HTTPException(status_code=500, detail=f"Alerts retrieval failed: {str(e)}")

@router.post("/cache/clear", dependencies=[Depends(requires_permission(Permission.MANAGE_POLICIES))])
async def clear_cache(pattern: Optional[str] = None):
    """Clear cache entries"""
    try:
        if pattern:
            # Clear specific pattern (simplified implementation)
            return {"message": f"Cache pattern '{pattern}' clearing not implemented in basic version"}
        else:
            # Clear all cache (for in-memory cache only)
            if hasattr(cache.redis_client, '_cache'):
                cache.redis_client._cache.clear()
                return {"message": "In-memory cache cleared successfully"}
            else:
                return {"message": "Redis cache clearing requires admin access"}
        
    except Exception as e:
        logger.error(f"Cache clear failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cache clear failed: {str(e)}")

@router.get("/system-info", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_system_info():
    """Get system information and configuration"""
    try:
        import psutil
        import os
        
        return {
            "system": {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage('/').percent
            },
            "configuration": {
                "cache_type": "redis" if hasattr(cache.redis_client, 'ping') else "in_memory",
                "performance_monitoring": "enabled",
                "alert_thresholds": monitor.alert_thresholds
            },
            "environment": {
                "redis_url": os.getenv("REDIS_URL", "not_configured"),
                "log_level": logging.getLogger().level
            }
        }
        
    except ImportError:
        return {
            "system": {"note": "System metrics require psutil package"},
            "configuration": {
                "cache_type": "redis" if hasattr(cache.redis_client, 'ping') else "in_memory",
                "performance_monitoring": "enabled",
                "alert_thresholds": monitor.alert_thresholds
            }
        }
    except Exception as e:
        logger.error(f"System info failed: {e}")
        raise HTTPException(status_code=500, detail=f"System info failed: {str(e)}")