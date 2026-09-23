"""Lock and unlock the payload.

This repository is public — GitHub Pages on the free tier requires it — so the
league's numbers never touch it in the clear. They live as AES-256-GCM
ciphertext under a key derived from a passphrase (PBKDF2-SHA256, 300k rounds),
both in the repo (the tape) and on the published site. The browser decrypts with
the same passphrase; anyone else gets a lock screen and a blob.

That is cryptography, not obscurity: the code is public, the data is not.

    python scripts/crypt.py lock   IN.json  OUT.json.enc
    python scripts/crypt.py unlock IN.json.enc OUT.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ITERATIONS = 300_000
KEY_BYTES = 32


def derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, ITERATIONS, KEY_BYTES)


def stable_salt(passphrase: str) -> bytes:
    """One salt for every file and every run, but unguessable without the
    passphrase (it is a hash OF it, so it cannot be precomputed). The browser
    then derives the key once instead of paying 300k rounds on every republish.
    """
    return hashlib.sha256(b"ev-model-v1" + passphrase.encode()).digest()[:16]


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise SystemExit("pip install cryptography")
    return AESGCM


def lock(plaintext: bytes, passphrase: str, salt: bytes | None = None) -> dict:
    salt = salt or stable_salt(passphrase)
    iv = os.urandom(12)                       # fresh per file, always
    ct = _aesgcm()(derive(passphrase, salt)).encrypt(iv, plaintext, None)
    b64 = lambda b: base64.b64encode(b).decode()      # noqa: E731
    return {"v": 1, "kdf": "pbkdf2-sha256", "iter": ITERATIONS, "cipher": "aes-256-gcm",
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def unlock(blob: dict, passphrase: str) -> bytes:
    key = derive(passphrase, base64.b64decode(blob["salt"]))
    return _aesgcm()(key).decrypt(base64.b64decode(blob["iv"]),
                                  base64.b64decode(blob["ct"]), None)


def passphrase_or_die() -> str:
    p = os.environ.get("EV_PASSPHRASE", "")
    if len(p) < 8:
        raise SystemExit("EV_PASSPHRASE is missing or shorter than 8 characters. "
                         "Nothing is published until it is set.")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["lock", "unlock"])
    ap.add_argument("source", type=Path)
    ap.add_argument("dest", type=Path)
    args = ap.parse_args()

    phrase = passphrase_or_die()
    args.dest.parent.mkdir(parents=True, exist_ok=True)
    if args.action == "lock":
        args.dest.write_text(json.dumps(lock(args.source.read_bytes(), phrase),
                                        separators=(",", ":")))
    else:
        args.dest.write_bytes(unlock(json.loads(args.source.read_text()), phrase))
    # deliberately says nothing about the contents: Actions logs are public here
    print(f"[{args.action}] {args.dest.name} ({args.dest.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
