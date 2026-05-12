import faiss
import numpy as np
from typing import List, Dict, Optional


class FaissService:
    """
    In-memory FAISS index for fast 1:N biometric identification.
    Maps internal student IDs to 512-d normalized face embeddings.
    """

    EMBEDDING_DIM = 512

    def __init__(self):
        # Inner-product index — equivalent to cosine similarity on normalized vectors
        self._index = faiss.IndexFlatIP(self.EMBEDDING_DIM)
        self._id_map: List[int] = []   # position → student.id

    def clear(self):
        """Reset the index (used on startup and re-enrollment)."""
        self._index.reset()
        self._id_map.clear()

    def add_student(self, student_id: int, embedding: np.ndarray):
        """Add a single normalized embedding to the index."""
        vec = embedding.astype(np.float32).reshape(1, -1)
        self._index.add(vec)
        self._id_map.append(student_id)

    def search(self, query_embedding: np.ndarray, top_k: int = 1) -> List[Dict]:
        """
        Search the index for the top-k most similar students.
        Returns a list of dicts with 'student_id' and 'confidence' keys.
        """
        if self._index.ntotal == 0:
            return []

        vec = query_embedding.astype(np.float32).reshape(1, -1)
        top_k = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(vec, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            results.append({
                "student_id": self._id_map[idx],
                "confidence": float(dist),
            })
        return results

    @property
    def index(self):
        return self._index


# Singleton
_faiss_service: Optional[FaissService] = None


def get_faiss_service() -> FaissService:
    global _faiss_service
    if _faiss_service is None:
        _faiss_service = FaissService()
    return _faiss_service
