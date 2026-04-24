import io
from PIL import Image
import hashlib
import numpy as np

def validate_image(file_content: bytes) -> bool:
    """
    Validate that the uploaded file is a readable image and meets minimum requirements.
    """
    try:
        img = Image.open(io.BytesIO(file_content))
        img.verify()
        
        # Check resolution (example: min 224x224)
        img = Image.open(io.BytesIO(file_content)) # Re-open because verify() closes it
        width, height = img.size
        if width < 200 or height < 200:
            return False
            
        return True
    except Exception:
        return False

def get_image_hash(file_content: bytes) -> str:
    """Generate a SHA-256 hash for auditability/integrity."""
    return hashlib.sha256(file_content).hexdigest()

def extract_face_template(file_content: bytes) -> bytes:
    """
    Simulate face template extraction. 
    In a real scenario, this would call a model (e.g. InsightFace) 
    and return a feature vector (e.g. 512 float values).
    """
    # For simulation, we generate a deterministic pseudo-template based on the image content hash.
    # This allows us to test 1:1 matching reliably.
    hasher = hashlib.sha256(file_content).digest()
    # Expand 32 bytes to 512 floats (just repeating for simulation)
    seed = int.from_bytes(hasher[:4], "big")
    np.random.seed(seed)
    simulated_vector = np.random.rand(512).astype(np.float32)
    return simulated_vector.tobytes()

def compare_faces(captured_template_bytes: bytes, stored_template_bytes: bytes) -> float:
    """
    Simulate 1:1 face matching (Cosine Similarity).
    """
    v1 = np.frombuffer(captured_template_bytes, dtype=np.float32)
    v2 = np.frombuffer(stored_template_bytes, dtype=np.float32)
    
    # Calculate cosine similarity
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    
    similarity = dot_product / (norm_v1 * norm_v2)
    return float(similarity)
