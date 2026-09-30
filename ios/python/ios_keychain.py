"""Keychain for the iPad app: generic passwords through Security.framework (ctypes).

Same service/account names as the Mac, so webapp.py does not care where it runs.
iOS only has the data-protection keychain; on macOS (tests) the same calls use the
login keychain.
"""
from __future__ import annotations

import ctypes
from ctypes import byref, c_char_p, c_long, c_void_p

_sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_UTF8 = 0x08000100
_NOT_FOUND = -25300          # errSecItemNotFound

_cf.CFStringCreateWithCString.restype = c_void_p
_cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, ctypes.c_uint32]
_cf.CFDataCreate.restype = c_void_p
_cf.CFDataCreate.argtypes = [c_void_p, c_char_p, c_long]
_cf.CFDataGetBytePtr.restype = c_void_p
_cf.CFDataGetBytePtr.argtypes = [c_void_p]
_cf.CFDataGetLength.restype = c_long
_cf.CFDataGetLength.argtypes = [c_void_p]
_cf.CFDictionaryCreate.restype = c_void_p
_cf.CFDictionaryCreate.argtypes = [c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p), c_long, c_void_p, c_void_p]
_cf.CFRelease.argtypes = [c_void_p]
for f in (_sec.SecItemCopyMatching, _sec.SecItemAdd):
    f.restype = ctypes.c_int32
    f.argtypes = [c_void_p, ctypes.POINTER(c_void_p)]
_sec.SecItemUpdate.restype = ctypes.c_int32
_sec.SecItemUpdate.argtypes = [c_void_p, c_void_p]
_sec.SecItemDelete.restype = ctypes.c_int32
_sec.SecItemDelete.argtypes = [c_void_p]


def _const(lib, name: str) -> c_void_p:
    return c_void_p.in_dll(lib, name)


_K = {n: _const(_sec, n) for n in (
    "kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount",
    "kSecValueData", "kSecReturnData", "kSecMatchLimit", "kSecMatchLimitOne",
    "kSecAttrAccessible", "kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly")}
_TRUE = _const(_cf, "kCFBooleanTrue")
_KEY_CB = ctypes.addressof(_const(_cf, "kCFTypeDictionaryKeyCallBacks"))
_VAL_CB = ctypes.addressof(_const(_cf, "kCFTypeDictionaryValueCallBacks"))


def _str(s: str) -> c_void_p:
    return c_void_p(_cf.CFStringCreateWithCString(None, s.encode(), _UTF8))


def _dict(pairs: list[tuple[c_void_p, c_void_p]]) -> c_void_p:
    n = len(pairs)
    keys = (c_void_p * n)(*[k.value for k, _ in pairs])
    vals = (c_void_p * n)(*[v.value for _, v in pairs])
    return c_void_p(_cf.CFDictionaryCreate(None, keys, vals, n, _KEY_CB, _VAL_CB))


def _query(account: str, service: str, owned: list) -> list[tuple[c_void_p, c_void_p]]:
    svc, acc = _str(service), _str(account)
    owned += [svc, acc]
    return [(_K["kSecClass"], _K["kSecClassGenericPassword"]),
            (_K["kSecAttrService"], svc), (_K["kSecAttrAccount"], acc)]


def _release(objs) -> None:
    for o in objs:
        if o and o.value:
            _cf.CFRelease(o)


def get(account: str, service: str = "eas-bridge") -> str | None:
    owned: list = []
    q = _dict(_query(account, service, owned) + [(_K["kSecReturnData"], _TRUE),
                                                   (_K["kSecMatchLimit"], _K["kSecMatchLimitOne"])])
    out = c_void_p()
    try:
        rc = _sec.SecItemCopyMatching(q, byref(out))
        if rc == _NOT_FOUND:
            return None
        if rc != 0:
            raise OSError(f"Keychain read failed ({rc})")
        n = _cf.CFDataGetLength(out)
        return ctypes.string_at(_cf.CFDataGetBytePtr(out), n).decode()
    finally:
        _release(owned + [q, out])


def set(account: str, service: str, password: str) -> None:  # noqa: A001 — mirrors the Mac helper
    owned: list = []
    raw = password.encode()
    data = c_void_p(_cf.CFDataCreate(None, raw, len(raw)))
    q = _dict(_query(account, service, owned))
    upd = _dict([(_K["kSecValueData"], data)])
    add = None
    try:
        rc = _sec.SecItemUpdate(q, upd)
        if rc == _NOT_FOUND:
            add = _dict(_query(account, service, owned) + [
                (_K["kSecValueData"], data),
                (_K["kSecAttrAccessible"], _K["kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly"])])
            rc = _sec.SecItemAdd(add, None)
        if rc != 0:
            raise OSError(f"Keychain write failed ({rc})")
    finally:
        _release(owned + [data, q, upd, add])


def delete(account: str, service: str = "eas-bridge") -> None:
    owned: list = []
    q = _dict(_query(account, service, owned))
    try:
        _sec.SecItemDelete(q)
    finally:
        _release(owned + [q])
