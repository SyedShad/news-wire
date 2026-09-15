"""Native macOS Keychain storage for the Telegram bot token.

The Telegram token is deliberately kept out of argv and environment variables.
The macOS backend uses Security.framework directly; the backend is loaded lazily
so importing the application and running Linux tests do not require macOS.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
from typing import Protocol


TELEGRAM_KEYCHAIN_SERVICE = "com.opensourceainewswire.telegram.bot-token"
_ACCOUNT_PREFIX = "runtime-"
_UTF8_ENCODING = 0x08000100
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_DUPLICATE_ITEM = -25299


class TelegramKeychainError(RuntimeError):
    """Raised when the Telegram token cannot be read or written safely."""


class KeychainBackend(Protocol):
    def get(self, service: str, account: str) -> str | None: ...

    def put(self, service: str, account: str, value: str) -> None: ...

    def delete(self, service: str, account: str) -> None: ...


class _SecurityBackend:
    """Small ctypes adapter around the modern SecItem API."""

    def __init__(self) -> None:
        if os.sys.platform != "darwin":
            raise TelegramKeychainError("macOS Keychain is unavailable on this platform")
        security_name = ctypes.util.find_library("Security")
        core_foundation_name = ctypes.util.find_library("CoreFoundation")
        if not security_name or not core_foundation_name:
            raise TelegramKeychainError("macOS Security.framework is unavailable")
        try:
            self.security = ctypes.CDLL(security_name)
            self.core_foundation = ctypes.CDLL(core_foundation_name)
            self._configure_functions()
        except (AttributeError, OSError) as error:
            raise TelegramKeychainError("macOS Security.framework could not be loaded") from error

        self.class_key = self._constant("kSecClass")
        self.class_generic_password = self._constant("kSecClassGenericPassword")
        self.attr_service = self._constant("kSecAttrService")
        self.attr_account = self._constant("kSecAttrAccount")
        self.value_data = self._constant("kSecValueData")
        self.return_data = self._constant("kSecReturnData")
        self.match_limit = self._constant("kSecMatchLimit")
        self.match_limit_one = self._constant("kSecMatchLimitOne")
        self.use_authentication_ui = self._constant("kSecUseAuthenticationUI")
        self.use_authentication_ui_fail = self._constant("kSecUseAuthenticationUIFail")
        self.boolean_true = self._cf_constant("kCFBooleanTrue")

    def _configure_functions(self) -> None:
        cf = self.core_foundation
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFDataCreate.restype = ctypes.c_void_p
        cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_ubyte)
        cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        cf.CFDataGetLength.restype = ctypes.c_long
        cf.CFDictionaryCreateMutable.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
        cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionarySetValue.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None

        self.security.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecItemAdd.restype = ctypes.c_int32
        self.security.SecItemCopyMatching.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecItemCopyMatching.restype = ctypes.c_int32
        self.security.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.security.SecItemUpdate.restype = ctypes.c_int32
        self.security.SecItemDelete.argtypes = [ctypes.c_void_p]
        self.security.SecItemDelete.restype = ctypes.c_int32

    def _constant(self, name: str) -> ctypes.c_void_p:
        return ctypes.c_void_p.in_dll(self.security, name)

    def _cf_constant(self, name: str) -> ctypes.c_void_p:
        return ctypes.c_void_p.in_dll(self.core_foundation, name)

    def _string(self, value: str) -> ctypes.c_void_p:
        encoded = value.encode("utf-8")
        result = self.core_foundation.CFStringCreateWithCString(None, encoded, _UTF8_ENCODING)
        if not result:
            raise TelegramKeychainError("Keychain string allocation failed")
        return ctypes.c_void_p(result)

    def _data(self, value: str) -> tuple[ctypes.c_void_p, ctypes.Array[ctypes.c_char]]:
        raw = value.encode("utf-8")
        buffer = ctypes.create_string_buffer(raw)
        result = self.core_foundation.CFDataCreate(None, ctypes.cast(buffer, ctypes.c_void_p), len(raw))
        if not result:
            raise TelegramKeychainError("Keychain data allocation failed")
        return ctypes.c_void_p(result), buffer

    def _dictionary(
        self,
        values: list[tuple[ctypes.c_void_p, ctypes.c_void_p]],
        *,
        owned: list[ctypes.c_void_p] | None = None,
    ) -> tuple[ctypes.c_void_p, list[ctypes.c_void_p]]:
        dictionary = self.core_foundation.CFDictionaryCreateMutable(None, 0, None, None)
        if not dictionary:
            for ref in owned or []:
                self.core_foundation.CFRelease(ref)
            raise TelegramKeychainError("Keychain dictionary allocation failed")
        refs = list(owned or [])
        try:
            for key, value in values:
                self.core_foundation.CFDictionarySetValue(dictionary, key, value)
        except Exception:
            self.core_foundation.CFRelease(dictionary)
            for ref in refs:
                self.core_foundation.CFRelease(ref)
            raise
        return ctypes.c_void_p(dictionary), refs

    def _base(self, service: str, account: str) -> tuple[ctypes.c_void_p, list[ctypes.c_void_p]]:
        service_ref = self._string(service)
        try:
            account_ref = self._string(account)
        except Exception:
            self.core_foundation.CFRelease(service_ref)
            raise
        return self._dictionary(
            [
                (self.class_key, self.class_generic_password),
                (self.attr_service, service_ref),
                (self.attr_account, account_ref),
            ],
            owned=[service_ref, account_ref],
        )

    def _release(self, dictionary: ctypes.c_void_p, refs: list[ctypes.c_void_p]) -> None:
        self.core_foundation.CFRelease(dictionary)
        for ref in refs:
            self.core_foundation.CFRelease(ref)

    def get(self, service: str, account: str) -> str | None:
        dictionary, refs = self._base(service, account)
        return_flag = self.boolean_true
        limit = self.match_limit_one
        self.core_foundation.CFDictionarySetValue(dictionary, self.return_data, return_flag)
        self.core_foundation.CFDictionarySetValue(dictionary, self.match_limit, limit)
        self.core_foundation.CFDictionarySetValue(
            dictionary, self.use_authentication_ui, self.use_authentication_ui_fail
        )
        result = ctypes.c_void_p()
        try:
            status = int(self.security.SecItemCopyMatching(dictionary, ctypes.byref(result)))
        finally:
            self._release(dictionary, refs)
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != 0 or not result.value:
            if result.value:
                self.core_foundation.CFRelease(result)
            raise TelegramKeychainError(f"Keychain read failed (OSStatus {status})")
        try:
            length = int(self.core_foundation.CFDataGetLength(result))
            pointer = self.core_foundation.CFDataGetBytePtr(result)
            return ctypes.string_at(pointer, length).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise TelegramKeychainError("Keychain value is not valid UTF-8") from error
        finally:
            self.core_foundation.CFRelease(result)

    def put(self, service: str, account: str, value: str) -> None:
        data_ref, data_buffer = self._data(value)
        try:
            query, query_refs = self._base(service, account)
        except Exception:
            self.core_foundation.CFRelease(data_ref)
            del data_buffer
            raise
        try:
            update, update_refs = self._dictionary([(self.value_data, data_ref)])
        except Exception:
            self._release(query, query_refs)
            self.core_foundation.CFRelease(data_ref)
            del data_buffer
            raise
        try:
            try:
                status = int(self.security.SecItemUpdate(query, update))
            except Exception:
                self.core_foundation.CFRelease(data_ref)
                del data_buffer
                raise
        finally:
            self._release(update, update_refs)
            self._release(query, query_refs)
        if status == 0:
            self.core_foundation.CFRelease(data_ref)
            del data_buffer
            return
        if status != _ERR_SEC_ITEM_NOT_FOUND:
            self.core_foundation.CFRelease(data_ref)
            del data_buffer
            raise TelegramKeychainError(f"Keychain update failed (OSStatus {status})")

        try:
            add_query, add_refs = self._base(service, account)
        except Exception:
            self.core_foundation.CFRelease(data_ref)
            del data_buffer
            raise
        self.core_foundation.CFDictionarySetValue(add_query, self.value_data, data_ref)
        result = ctypes.c_void_p()
        try:
            status = int(self.security.SecItemAdd(add_query, ctypes.byref(result)))
        finally:
            self._release(add_query, add_refs)
            self.core_foundation.CFRelease(data_ref)
            # Keep the Python buffer alive until SecItemAdd has returned.
            del data_buffer
        if status == _ERR_SEC_DUPLICATE_ITEM:
            raise TelegramKeychainError("Keychain update raced with another writer")
        if status != 0:
            raise TelegramKeychainError(f"Keychain write failed (OSStatus {status})")
        if result.value:
            self.core_foundation.CFRelease(result)

    def delete(self, service: str, account: str) -> None:
        query, refs = self._base(service, account)
        try:
            status = int(self.security.SecItemDelete(query))
        finally:
            self._release(query, refs)
        if status not in {0, _ERR_SEC_ITEM_NOT_FOUND}:
            raise TelegramKeychainError(f"Keychain delete failed (OSStatus {status})")


class TelegramKeychain:
    """Store one Telegram token for one local runtime root."""

    def __init__(self, runtime_root: str | os.PathLike[str], *, backend: KeychainBackend | None = None):
        root = Path(runtime_root).expanduser().resolve()
        self.account = _ACCOUNT_PREFIX + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
        self.backend = backend if backend is not None else _SecurityBackend()

    def get_token(self) -> str | None:
        return self.backend.get(TELEGRAM_KEYCHAIN_SERVICE, self.account)

    def store_token(self, token: str) -> None:
        value = token.strip()
        if not value or len(value) > 512 or any(character in value for character in "\r\n\x00"):
            raise TelegramKeychainError("Telegram bot token is invalid")
        self.backend.put(TELEGRAM_KEYCHAIN_SERVICE, self.account, value)

    def delete_token(self) -> None:
        self.backend.delete(TELEGRAM_KEYCHAIN_SERVICE, self.account)
