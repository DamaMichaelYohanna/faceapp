import faiss
import numpy as np
import threading
from typing import Optional, Tuple, List, Dict

class FAISSService:
    """
    Manages an in-memory FAISS index for 1:N identification.
    Uses IndexFlatIP for Cosine Similarity (on normalized vectors).
    """
    def __init__(self, dimension: int = 512):
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        # Mapping: Index ID -> Student ID
        self.id_map: List[int] = []
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self.index = faiss.IndexFlatIP(self.dimension)
            self.id_map = []

    def add_student(self, student_id: int, embedding: np.ndarray):
        """Add a single student embedding to the index."""
        if embedding.shape[0] != self.dimension:
            raise ValueError(f"Embedding dimension mismatch: {embedding.shape[0]} != {self.dimension}")
        
        with self._lock:
            # FAISS requires float32
            vector = embedding.astype('float32').reshape(1, -1)
            self.index.add(vector)
            self.id_map.append(student_id)

    def search(self, embedding: np.ndarray, top_k: int = 1) -> List[Dict]:
        """
        Search the index for the most similar embedding.
        Returns a list of dicts with student_id and confidence.
        """
        if self.index.ntotal == 0:
            return []

        vector = embedding.astype('float32').reshape(1, -1)
        distances, indices = self.index.search(vector, top_k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1:
                results.append({
                    "student_id": self.id_map[idx],
                    "confidence": float(dist)
                })
        return results

# Singleton instance
faiss_service = FAISSService()

def get_faiss_service():
    return faiss_service
