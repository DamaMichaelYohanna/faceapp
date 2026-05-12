import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# Encryption key — MUST match the capturing service's BIOMETRIC_SECRET_KEY
ENCRYPTION_KEY = os.getenv("BIOMETRIC_SECRET_KEY", Fernet.generate_key().decode())
_cipher = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)


def decrypt_data(encrypted_data: bytes) -> bytes:
    """Decrypt a face embedding that was encrypted by the capturing service."""
    return _cipher.decrypt(encrypted_data)
