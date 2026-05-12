import os
import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis


class FaceEngine:
    def __init__(self, model_name="buffalo_l"):
        """
        Initialize the InsightFace engine.
        Using CPU-only mode by default.
        """
        self.app = FaceAnalysis(name=model_name, providers=['CPUExecutionProvider'])
        self.app.prepare(ctx_id=0, det_size=(640, 640))
        print(f"FaceEngine initialized with model: {model_name}")

    def get_embedding(self, image_bytes: bytes) -> np.ndarray:
        """
        Extract 512-d embedding from an image.
        Returns the most prominent face detected, or None if no face found.
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise ValueError("Invalid image data")

        faces = self.app.get(img)

        if not faces:
            return None

        # Sort by bounding-box area to get the most prominent face
        faces = sorted(
            faces,
            key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]),
            reverse=True,
        )

        embedding = faces[0].embedding

        # Normalize for cosine similarity
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding


# Singleton instance
_engine = None


def get_face_engine() -> FaceEngine:
    global _engine
    if _engine is None:
        _engine = FaceEngine()
    return _engine
