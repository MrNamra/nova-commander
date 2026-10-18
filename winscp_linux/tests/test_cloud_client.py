"""Unit tests for CloudClient upload and download functionality."""

import pytest
from src.cloud.cloud_client import CloudAPIError, CloudClient, UploadResult
from src.crypto.ini_cipher import encrypt_to_base64, decrypt_from_base64


def test_cloud_upload_and_download_mock():
    client = CloudClient(use_mock_fallback=True)
    raw_ini = "[Sessions\\Test]\nHostName=192.168.1.1\nUserName=test"
    password = "MasterPassword999"

    encrypted_payload = encrypt_to_base64(raw_ini, password)
    result = client.upload_encrypted_config(encrypted_payload, retention_days=7)

    assert isinstance(result, UploadResult)
    assert result.share_id != ""
    assert result.expires_in_days == 7

    # Download back using share_id
    downloaded_payload = client.download_encrypted_config(result.share_id)
    assert downloaded_payload == encrypted_payload

    # Decrypt and verify
    decrypted = decrypt_from_base64(downloaded_payload, password)
    assert decrypted == raw_ini


def test_cloud_invalid_share_id():
    client = CloudClient(use_mock_fallback=True)
    with pytest.raises(CloudAPIError):
        client.download_encrypted_config("non_existent_share_id_999999")


def test_cloud_empty_payload_raises():
    client = CloudClient()
    with pytest.raises(ValueError):
        client.upload_encrypted_config("")
