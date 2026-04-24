import os
from cryptography.fernet import Fernet
from typing import Optional
from jose import JWTError, jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Encryption Key for Biometric Templates
# In production, this should be a robust environment variable or secret manager
ENCRYPTION_KEY = os.getenv("BIOMETRIC_SECRET_KEY", Fernet.generate_key().decode())
cipher_suite = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)

# JWT Settings
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-super-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def encrypt_data(data: bytes) -> bytes:
    """Encrypt sensitive biometric data."""
    return cipher_suite.encrypt(data)

def decrypt_data(encrypted_data: bytes) -> bytes:
    """Decrypt sensitive biometric data."""
    return cipher_suite.decrypt(encrypted_data)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
