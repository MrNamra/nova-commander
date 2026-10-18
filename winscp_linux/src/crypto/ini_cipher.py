"""
AES-256-GCM encryption and decryption module for WinSCP-Linux session configurations.
Meets strict security specifications:
- Key Derivation: PBKDF2HMAC with SHA-256, 16-byte random salt, 600,000 iterations.
- Symmetric Cipher: AES-256 in GCM mode.
- Nonce/IV: 16-byte cryptographically secure random bytes.
- Binary Layout: SALT (16B) + IV (16B) + CIPHERTEXT + GCM_TAG (16B).
- Base64 encoding/decoding for cloud and export transmission.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

SALT_SIZE: Final[int] = 16
IV_SIZE: Final[int] = 16
TAG_SIZE: Final[int] = 16
KEY_SIZE: Final[int] = 32  # 256 bits
PBKDF2_ITERATIONS: Final[int] = 600_000
MIN_PAYLOAD_SIZE: Final[int] = SALT_SIZE + IV_SIZE + TAG_SIZE  # 48 bytes


class CryptoError(Exception):
    """Base exception for cryptographic operations."""


class DecryptionError(CryptoError):
    """Raised when decryption fails due to invalid password or corrupted data."""


class InvalidPayloadError(CryptoError):
    """Raised when ciphertext payload is malformed or truncated."""


def derive_key(password: str | bytes, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """
    Derives a 256-bit symmetric key from a master password and salt using PBKDF2HMAC-SHA256.

    Args:
        password: The master password string or bytes.
        salt: 16-byte cryptographic salt.
        iterations: Number of PBKDF2 rounds (defaults to 600,000).

    Returns:
        32-byte derived key.
    """
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")
    else:
        password_bytes = password

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password_bytes)


def encrypt_data(data: bytes, password: str | bytes) -> bytes:
    """
    Encrypts raw bytes using AES-256-GCM with key derived via PBKDF2HMAC.

    Packaging layout:
        [SALT: 16 bytes] + [IV: 16 bytes] + [CIPHERTEXT] + [TAG: 16 bytes]
    (Note: AESGCM.encrypt appends the 16-byte authentication tag directly to ciphertext)

    Args:
        data: Plaintext bytes to encrypt.
        password: User's master password.

    Returns:
        Combined binary payload.
    """
    salt = os.urandom(SALT_SIZE)
    iv = os.urandom(IV_SIZE)
    key = derive_key(password, salt)

    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(iv, data, None)

    return salt + iv + ciphertext_and_tag


def decrypt_data(payload: bytes, password: str | bytes) -> bytes:
    """
    Decrypts binary payload produced by encrypt_data using AES-256-GCM.

    Args:
        payload: Binary blob consisting of SALT(16B) + IV(16B) + CIPHERTEXT + TAG(16B).
        password: User's master password.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        InvalidPayloadError: If payload is shorter than minimum required length.
        DecryptionError: If master password is incorrect or data has been altered.
    """
    if len(payload) < MIN_PAYLOAD_SIZE:
        raise InvalidPayloadError(
            f"Payload too short ({len(payload)} bytes). Minimum required is {MIN_PAYLOAD_SIZE} bytes."
        )

    salt = payload[:SALT_SIZE]
    iv = payload[SALT_SIZE : SALT_SIZE + IV_SIZE]
    ciphertext_and_tag = payload[SALT_SIZE + IV_SIZE :]

    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        plaintext = aesgcm.decrypt(iv, ciphertext_and_tag, None)
        return plaintext
    except InvalidTag as exc:
        raise DecryptionError("Decryption failed: incorrect master password or corrupted payload.") from exc


def encrypt_to_base64(plaintext: str, password: str) -> str:
    """
    Encrypts a string (such as WinSCP INI configuration) and returns a Base64 encoded payload.

    Args:
        plaintext: UTF-8 string to encrypt.
        password: Master encryption password.

    Returns:
        ASCII Base64 string representing encrypted binary payload.
    """
    data_bytes = plaintext.encode("utf-8")
    binary_payload = encrypt_data(data_bytes, password)
    return base64.b64encode(binary_payload).decode("ascii")


def decrypt_from_base64(b64_payload: str, password: str) -> str:
    """
    Decodes a Base64 string and decrypts it back to a UTF-8 string.

    Args:
        b64_payload: Base64-encoded encrypted payload.
        password: Master encryption password.

    Returns:
        Decrypted UTF-8 string.

    Raises:
        InvalidPayloadError: If Base64 decoding fails or payload structure is invalid.
        DecryptionError: If password is wrong or ciphertext tag verification fails.
    """
    try:
        binary_payload = base64.b64decode(b64_payload.strip().encode("ascii"), validate=True)
    except Exception as exc:
        raise InvalidPayloadError(f"Invalid Base64 payload: {exc}") from exc

    plaintext_bytes = decrypt_data(binary_payload, password)
    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptionError("Payload decrypted but could not decode as valid UTF-8 string.") from exc
