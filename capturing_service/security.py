import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# Encryption key for biometric templates — must match the verification service
ENCRYPTION_KEY = os.getenv("BIOMETRIC_SECRET_KEY", Fernet.generate_key().decode())
_cipher = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)


def encrypt_data(data: bytes) -> bytes:
    """Encrypt raw bytes (e.g., a face embedding) for secure storage."""
    return _cipher.encrypt(data)


def decrypt_data(encrypted_data: bytes) -> bytes:
    """Decrypt previously encrypted bytes."""
    return _cipher.decrypt(encrypted_data)
