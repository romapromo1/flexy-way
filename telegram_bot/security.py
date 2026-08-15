from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
from ctypes import wintypes
from pathlib import Path


class DataProtectionError(RuntimeError):
    pass


_DPAPI_PREFIX = "dpapi:v1:"
_LOCAL_PREFIX = "local:v1:"
_SECRET_PREFIX = "secret:v1:"
_MACHINE_SECRET_PREFIX = "secret-machine:v1:"
_ENTROPY = b"FlexyWayTelegramBot:v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes, *, local_machine: bool = False) -> bytes:
    if os.name != "nt":
        raise DataProtectionError("Windows DPAPI недоступен")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    flags = 0x1 | (0x4 if local_machine else 0)
    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        flags,
        ctypes.byref(result),
    )
    del source_buffer, entropy_buffer
    if not ok:
        raise DataProtectionError(f"DPAPI не смог зашифровать данные: {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise DataProtectionError("Windows DPAPI недоступен")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    for ent in (ctypes.byref(entropy), None):
        for flags in (0x1, 0x5, 0x0, 0x4):
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(source),
                None,
                ent,
                None,
                None,
                flags,
                ctypes.byref(result),
            )
            if ok:
                del source_buffer, entropy_buffer
                try:
                    return ctypes.string_at(result.pbData, result.cbData)
                finally:
                    kernel32.LocalFree(result.pbData)
    del source_buffer, entropy_buffer
    raise DataProtectionError(f"DPAPI не смог расшифровать данные: {ctypes.GetLastError()}")


def _stream_cipher(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray(len(data))
    for block_index, offset in enumerate(range(0, len(data), 32)):
        stream = hmac.new(
            key,
            b"enc\0" + nonce + block_index.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        chunk = data[offset : offset + 32]
        output[offset : offset + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, stream))
    return bytes(output)


class LocalDataProtector:
    """Protects local bot data and creates non-reversible lookup identifiers.

    On Windows, both the master secret and every encrypted value are protected by
    DPAPI and can only be opened by the same Windows account. The authenticated
    local fallback exists for tests and non-Windows development only.
    """

    def __init__(self, secret_path: Path):
        self.secret_path = Path(secret_path)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create_secret()

    def _load_or_create_secret(self) -> bytes:
        if self.secret_path.exists():
            encoded = self.secret_path.read_text(encoding="ascii").strip()
            if encoded.startswith(_MACHINE_SECRET_PREFIX):
                payload = base64.urlsafe_b64decode(
                    encoded.removeprefix(_MACHINE_SECRET_PREFIX)
                )
                secret = _dpapi_unprotect(payload) if os.name == "nt" else payload
            elif encoded.startswith(_SECRET_PREFIX):
                payload = base64.urlsafe_b64decode(encoded.removeprefix(_SECRET_PREFIX))
                secret = _dpapi_unprotect(payload) if os.name == "nt" else payload
                # Older installations protected the lookup key for one process token.
                # Machine-scoped DPAPI keeps background and interactive processes compatible;
                # access to the key file is still restricted by the user profile ACL.
                self._store_secret(secret)
            else:
                raise DataProtectionError("Файл локального секрета имеет неизвестный формат")
            if len(secret) != 32:
                raise DataProtectionError("Файл локального секрета повреждён")
            return secret

        secret = secrets.token_bytes(32)
        self._store_secret(secret)
        return secret

    def _store_secret(self, secret: bytes) -> None:
        stored = (
            _dpapi_protect(secret, local_machine=True) if os.name == "nt" else secret
        )
        prefix = _MACHINE_SECRET_PREFIX if os.name == "nt" else _SECRET_PREFIX
        temp_path = self.secret_path.with_name(self.secret_path.name + ".tmp")
        temp_path.write_text(
            prefix + base64.urlsafe_b64encode(stored).decode("ascii"),
            encoding="ascii",
        )
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, self.secret_path)

    def lookup(self, kind: str, value: str | int) -> str:
        normalized = str(value).strip().encode("utf-8")
        return hmac.new(
            self._secret,
            kind.encode("ascii") + b"\0" + normalized,
            hashlib.sha256,
        ).hexdigest()

    def reference(self, kind: str, value: str | int, length: int = 12) -> str:
        return f"{kind}_{self.lookup(kind, value)[:length]}"

    def encrypt(self, value: str | int | None) -> str | None:
        if value is None or value == "":
            return None
        plaintext = str(value).encode("utf-8")
        if os.name == "nt":
            payload = _dpapi_protect(plaintext)
            return _DPAPI_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")

        nonce = secrets.token_bytes(16)
        ciphertext = _stream_cipher(plaintext, self._secret, nonce)
        tag = hmac.new(self._secret, b"tag\0" + nonce + ciphertext, hashlib.sha256).digest()
        return _LOCAL_PREFIX + base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")

    def decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        if value.startswith(_DPAPI_PREFIX):
            payload = base64.urlsafe_b64decode(value.removeprefix(_DPAPI_PREFIX))
            try:
                return _dpapi_unprotect(payload).decode("utf-8")
            except Exception:
                return ""
        if value.startswith(_LOCAL_PREFIX):
            payload = base64.urlsafe_b64decode(value.removeprefix(_LOCAL_PREFIX))
            nonce, tag, ciphertext = payload[:16], payload[16:48], payload[48:]
            expected = hmac.new(
                self._secret, b"tag\0" + nonce + ciphertext, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(tag, expected):
                return ""
            return _stream_cipher(ciphertext, self._secret, nonce).decode("utf-8")
        # Read-only compatibility for values written by the first bot version.
        return value


def mask_phone(phone: str | None) -> str:
    digits = "".join(character for character in str(phone or "") if character.isdigit())
    if not digits:
        return ""
    visible = digits[-4:]
    return "+" + "•" * max(0, len(digits) - len(visible)) + visible
