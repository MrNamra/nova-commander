"""Unit tests for AES-256-GCM encryption & decryption in ini_cipher.py."""

import base64
import pytest
from src.crypto.ini_cipher import (
    DecryptionError,
    InvalidPayloadError,
    SALT_SIZE,
    IV_SIZE,
    TAG_SIZE,
    decrypt_data,
    decrypt_from_base64,
    encrypt_data,
    encrypt_to_base64,
)

SAMPLE_INI = """[Sessions\\Production_Web]
HostName=192.168.1.50
PortNumber=22
UserName=deploy
Password=SecretPassword123!
FSProtocol=0
RemoteDirectory=/var/www/html

[Sessions\\Backup_Storage]
HostName=10.0.0.99
PortNumber=2222
UserName=backup_admin
Password=AnotherStrongPassword987
FSProtocol=0
"""


def test_roundtrip_bytes():
    password = "MasterKey#2026_Secure!"
    plaintext = b"Sample SSH configuration bytes \x00\xff with special chars."
    payload = encrypt_data(plaintext, password)

    assert len(payload) >= SALT_SIZE + IV_SIZE + TAG_SIZE + len(plaintext)
    decrypted = decrypt_data(payload, password)
    assert decrypted == plaintext


def test_roundtrip_base64_ini():
    password = "CorrectHorseBatteryStaple"
    b64_cipher = encrypt_to_base64(SAMPLE_INI, password)

    assert isinstance(b64_cipher, str)
    # Check that it's valid base64
    raw_payload = base64.b64decode(b64_cipher)
    assert len(raw_payload) >= SALT_SIZE + IV_SIZE + TAG_SIZE

    decrypted_ini = decrypt_from_base64(b64_cipher, password)
    assert decrypted_ini == SAMPLE_INI


def test_non_deterministic_output():
    password = "ConstantPassword"
    text = "Identical text encrypted twice"

    payload1 = encrypt_to_base64(text, password)
    payload2 = encrypt_to_base64(text, password)

    # Different salts and IVs ensure different outputs
    assert payload1 != payload2

    # Both decrypt correctly
    assert decrypt_from_base64(payload1, password) == text
    assert decrypt_from_base64(payload2, password) == text


def test_wrong_password_fails():
    password = "CorrectPassword123"
    wrong_password = "WrongPassword456"

    payload = encrypt_to_base64(SAMPLE_INI, password)
    with pytest.raises(DecryptionError):
        decrypt_from_base64(payload, wrong_password)


def test_tampered_ciphertext_fails():
    password = "TamperDetectionPassword"
    payload = bytearray(encrypt_data(b"Sensitive payload", password))

    # Tamper with a byte in the middle of ciphertext
    payload[35] ^= 0xFF

    with pytest.raises(DecryptionError):
        decrypt_data(bytes(payload), password)


def test_tampered_tag_fails():
    password = "TagIntegrityPassword"
    payload = bytearray(encrypt_data(b"Sensitive payload", password))

    # Tamper with the last byte (part of authentication tag)
    payload[-1] ^= 0x01

    with pytest.raises(DecryptionError):
        decrypt_data(bytes(payload), password)


def test_truncated_payload_fails():
    password = "TruncationPassword"
    payload = encrypt_data(b"Short", password)

    # Payload shorter than minimum 48 bytes
    truncated = payload[:30]
    with pytest.raises(InvalidPayloadError):
        decrypt_data(truncated, password)


def test_invalid_base64_string():
    password = "AnyPassword"
    with pytest.raises(InvalidPayloadError):
        decrypt_from_base64("This is definitely not valid base64!@#$%", password)


def test_unicode_and_symbols():
    password = "🔑Пароль-パスワード-Passwort-123"
    content = "Host=сервер.example.com\nUser=äöü-admin\nNotes=🚀✨"
    b64 = encrypt_to_base64(content, password)
    assert decrypt_from_base64(b64, password) == content
