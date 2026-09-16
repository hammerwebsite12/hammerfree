"""HWID obfuscation — Python port of Hammer's HwidObfuscation.cs.

This MUST stay byte-for-byte compatible with the C# implementation so that
the activation worker (which decodes the 42-char code back into the device
UUID) can process QuickPlay registration codes exactly like Hammer's.

Format of the 42-char code: [5 random digits][32 obfuscated hex][5 random digits]
Obfuscation = XOR each of the 16 UUID bytes with a fixed key, then permute the
resulting 32 hex characters with a fixed permutation table.
"""

from __future__ import annotations

import re
import secrets

# 16-byte key in hex (32 hex chars). Keep PRIVATE and consistent with the
# encoder/decoder used by the activation worker.
_KEY_HEX = "A1B2C3D4E5F60718293A4B5C6D7E8F90"

# Permutation for the 32 hex characters (positions 0..31).
_PERM32 = (
    13, 27, 2, 19, 31, 7, 22, 0,
    17, 5, 29, 10, 23, 14, 8, 3,
    28, 21, 11, 24, 16, 30, 6, 26,
    1, 18, 12, 25, 9, 20, 15, 4,
)

_UUID_SCAN = re.compile(
    r"[{(]?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})[)}]?"
)
_RAW32 = re.compile(r"^[0-9A-Fa-f]{32}$")


def _extract_hex32(uuid_input: str) -> str:
    """Extract exactly 32 upper-hex chars from a UUID string (dashed or raw)."""
    trimmed = (uuid_input or "").strip()

    scan = _UUID_SCAN.search(trimmed)
    if scan:
        return scan.group(1).replace("-", "").upper()

    if len(trimmed) == 32 and _RAW32.match(trimmed):
        return trimmed.upper()

    raise ValueError(
        "Input is not a valid UUID. Expected "
        "'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' or 32 raw hex characters. "
        f"Got: '{trimmed}'"
    )


def _permute(value: str, perm: tuple[int, ...]) -> str:
    if len(value) != len(perm):
        raise ValueError("Permutation length mismatch.")
    return "".join(value[perm[i]] for i in range(len(perm)))


def _unpermute(value: str, perm: tuple[int, ...]) -> str:
    if len(value) != len(perm):
        raise ValueError("Permutation length mismatch.")
    out = [""] * len(perm)
    for i in range(len(perm)):
        out[perm[i]] = value[i]
    return "".join(out)


def _random_digits(length: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def encode_uuid_to_code42(uuid_input: str) -> str:
    """Encode a 32/36-char UUID into the 42-char registration code."""
    hex32 = _extract_hex32(uuid_input)
    hwid_bytes = bytes.fromhex(hex32)
    key_bytes = bytes.fromhex(_KEY_HEX)

    xored = bytes(hwid_bytes[i] ^ key_bytes[i] for i in range(16))
    xored_hex = xored.hex().upper()

    obf_hex32 = _permute(xored_hex, _PERM32)
    return _random_digits(5) + obf_hex32 + _random_digits(5)


def decode_code42_to_uuid(code42: str, dashed: bool = True) -> str:
    """Decode the 42-char registration code back into the original UUID.

    Mainly for testing/verification; the live activation worker performs the
    authoritative decode server-side.
    """
    cleaned = re.sub(r"\s+", "", code42 or "")
    if len(cleaned) != 42:
        raise ValueError(
            f"Code must be exactly 42 characters, got {len(cleaned)}."
        )

    obf_hex32 = cleaned[5:37]
    xored_hex = _unpermute(obf_hex32, _PERM32)

    xored = bytes.fromhex(xored_hex)
    key_bytes = bytes.fromhex(_KEY_HEX)
    hwid_bytes = bytes(xored[i] ^ key_bytes[i] for i in range(16))

    hex32 = hwid_bytes.hex().upper()
    if not dashed:
        return hex32
    return (
        f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-"
        f"{hex32[16:20]}-{hex32[20:32]}"
    )
