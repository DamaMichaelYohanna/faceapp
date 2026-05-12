"""
aes_crypto.py
AES-256-CBC encryption helpers for the master-server upload payload.

The SECRET (AES key) is stored in SystemConfig.aes_secret.
Encrypted fields:
  - 'user' : AES-encrypt(operator_username)
  - 'data' : AES-encrypt(base64(raw_template_bytes))

Wire format: base64( IV[16] || PKCS7-padded-ciphertext )
"""

import base64
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding


def _key_from_secret(secret: str) -> bytes:
    """
    Derive a 32-byte AES key from the stored secret string.
    Tries base64-decode first (common KMS format); falls back to
    UTF-8 bytes zero-padded / truncated to 32 bytes.
    """
    try:
        decoded = base64.b64decode(secret + "==")  # lenient padding
        if len(decoded) >= 16:
            return (decoded + b"\x00" * 32)[:32]
    except Exception:
        pass
    raw = secret.encode("utf-8")
    return (raw + b"\x00" * 32)[:32]


def aes_encrypt(plaintext: str, secret: str) -> str:
    """
    AES-256-CBC encrypt a UTF-8 string.
    Returns base64( IV[16] || PKCS7-ciphertext ).
    """
    key = _key_from_secret(secret)
    iv = os.urandom(16)

    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ciphertext = enc.update(padded) + enc.finalize()

    return base64.b64encode(iv + ciphertext).decode("utf-8")


def encrypt_template(raw_bytes: bytes, secret: str) -> str:
    """
    Prepare a face-template for upload:
      1. base64-encode the raw bytes
      2. AES-encrypt the base64 string
    Returns the encrypted string ready for prints[].data.
    """
    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    return aes_encrypt(b64, secret)


def encrypt_username(username: str, secret: str) -> str:
    """Encrypt the operator username for the 'user' field."""
    return aes_encrypt(username, secret)
