import io
import base64
from PIL import Image
import hashlib

def validate_image(file_content: bytes) -> bool:
    """
    Validate that the uploaded file is a readable image and meets minimum requirements.
    This acts as a lightweight pre-check before AI processing.
    """
    try:
        img = Image.open(io.BytesIO(file_content))
        img.verify()
        
        # Re-open because verify() closes the file pointer
        img = Image.open(io.BytesIO(file_content))
        width, height = img.size
        
        # Minimum resolution for reliable biometric extraction
        if width < 200 or height < 200:
            return False
            
        return True
    except Exception:
        return False

def get_image_hash(file_content: bytes) -> str:
    """
    Generate a SHA-256 hash of the image content.
    Used for audit trails and ensuring the integrity of enrolled data.
    """
    return hashlib.sha256(file_content).hexdigest()


def make_thumbnail(image_bytes: bytes, size: int = 120) -> str | None:
    """Return a data-URI JPEG thumbnail (≤size×size px), or None on failure."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
