"""
Gemini 1536-Dimensional Embedding Service
Centralized service using google.genai for 1536-dim embeddings
"""

from google import genai
from google.genai import types
import os
from typing import List
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class GeminiEmbeddingService:
    """Singleton service for Gemini 1536-dimensional embeddings"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required for production")
        
        # Use Google GenAI client for 1536 dimensions
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-embedding-001"
        self.dimensions = 1536
        self._initialized = True
    
    def embed_query(self, text: str) -> List[float]:
        """Generate 1536-dimensional embedding for text with retry backoff"""
        import time
        max_retries = 4
        delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                result = self.client.models.embed_content(
                    model=self.model,
                    contents=text,
                    config=types.EmbedContentConfig(output_dimensionality=self.dimensions)
                )
                [embedding_obj] = result.embeddings
                return list(embedding_obj.values)
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = any(k in err_msg for k in ["429", "resourceexhausted", "quota", "rate limit", "temporarily unavailable"])
                if is_rate_limit and attempt < max_retries:
                    logger.warning(f"Gemini embedding rate-limited (attempt {attempt}/{max_retries}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                    delay *= 2.0
                elif attempt < max_retries and ("timeout" in err_msg or "connection" in err_msg):
                    logger.warning(f"Gemini embedding network glitch (attempt {attempt}/{max_retries}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                    delay *= 2.0
                else:
                    logger.error(f"Gemini embedding failed on attempt {attempt}: {e}")
                    raise RuntimeError(f"Embedding generation failed: {e}")
        raise RuntimeError("Embedding generation failed after max retries")
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts"""
        try:
            return [self.embed_query(text) for text in texts]
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            raise RuntimeError(f"Batch embedding generation failed: {e}")

    def generate_embedding(self, text: str) -> List[float]:
        """Synchronous embedding generation for standard service callers"""
        return self.embed_query(text)

    async def generate_embedding_async(self, text: str) -> List[float]:
        """Asynchronous embedding generation using thread pool"""
        import asyncio
        return await asyncio.to_thread(self.embed_query, text)

# Global instance for backward compatibility
embedding = GeminiEmbeddingService()