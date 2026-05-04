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
        self.app.prepare(ctx_id=0, det_size=(320, 320))
        print(f"FaceEngine initialized with model: {model_name}")

    def get_embedding(self, image_bytes: bytes) -> np.ndarray:
        """
        Extract 512-d embedding from an image.
        Returns the first face detected.
        """
        # Convert bytes to numpy array (OpenCV format)
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Invalid image data")

        # Detect and analyze
        faces = self.app.get(img)
        
        if not faces:
            return None
        
        # Sort by box size to get the most prominent face
        faces = sorted(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]), reverse=True)
        
        # Return the 512-d embedding
        embedding = faces[0].embedding
        
        # Normalize for cosine similarity
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
            
        return embedding


class FastFaceDetector:
    """
    Lightweight face detector for real-time WebSocket liveness checks.
    Uses a smaller detection size (160x160) and skips embedding extraction,
    making it ~10-15x faster than full get_embedding() on CPU.
    """
    def __init__(self, model_name="buffalo_l"):
        self.app = FaceAnalysis(name=model_name, allowed_modules=['detection'], providers=['CPUExecutionProvider'])
        self.app.prepare(ctx_id=0, det_size=(160, 160))
        print(f"FastFaceDetector initialized (det_size=160x160, detection-only)")

    def has_face(self, image_bytes: bytes) -> bool:
        """Return True if at least one face is detected. No embedding extraction."""
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return False
        faces = self.app.get(img)
        return len(faces) > 0


# Singleton instances
engine = None
fast_detector = None

def get_face_engine():
    global engine
    if engine is None:
        engine = FaceEngine()
    return engine

def get_fast_face_detector():
    """Lightweight detector singleton for real-time WebSocket face checks."""
    global fast_detector
    if fast_detector is None:
        fast_detector = FastFaceDetector()
    return fast_detector

