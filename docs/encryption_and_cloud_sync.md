# Encrypted .ini Export and Cloud Sync

## 1. Export Flow and Selection
*   When "Export to Cloud" is clicked, display a checklist of all saved sessions.
*   The user selects which sessions to include and is prompted to set a **Master Export Password**.
*   Generate an INI-formatted string containing ONLY the selected sessions.

## 2. Encryption Standard (CRITICAL)
The `.ini` payload must be encrypted before transmission.
*   **Key Derivation:** Use PBKDF2HMAC (SHA256, 16-byte random salt, 600,000 iterations) with the Master Password.
*   **Algorithm:** AES-256 in GCM mode.
*   **Packaging:** The final binary blob must be: `SALT (16 bytes) + IV (16 bytes) + CIPHERTEXT + GCM_TAG (16 bytes)`.
*   Convert this blob to Base64 for the API payload.

## 3. Cloud API Spec
*   **Upload:** `POST https://your-cloud-endpoint/upload`
    *   JSON Body: `{"payload": "BASE64_STRING", "retention": "7d"}`
    *   Response: `{"share_url": "...", "share_id": "..."}`
*   **Download:** `GET https://your-cloud-endpoint/download/<share_id>`
    *   Response: `{"payload": "BASE64_STRING"}`
*   The application must prompt the user for the Master Password, decrypt the payload, and append the imported sessions to the local configuration.