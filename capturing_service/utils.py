import io
from PIL import Image
import hashlib


def validate_image(file_content: bytes) -> bool:
    """
    Validate that the uploaded file is a readable image and meets minimum
    resolution requirements for reliable biometric extraction.
    """
    try:
        img = Image.open(io.BytesIO(file_content))
        img.verify()

        # Re-open because verify() closes the file pointer
        img = Image.open(io.BytesIO(file_content))
        width, height = img.size

        if width < 200 or height < 200:
            return False

        return True
    except Exception:
        return False


def get_image_hash(file_content: bytes) -> str:
    """SHA-256 hash of raw image bytes — used for audit trail integrity."""
    return hashlib.sha256(file_content).hexdigest()
