#!/usr/bin/env python3
"""Encrypt books-private.json with the admin password → deployable artifact.

Produces data/books-private.json.enc containing:
{
  "v": 1,
  "kdf": "pbkdf2-sha256",
  "iterations": 310000,
  "salt": <base64 16B>,
  "iv": <base64 12B>,
  "ct": <base64 AES-256-GCM ciphertext>,
}

The browser derives the same key from the login password via WebCrypto
(PBKDF2 → AES-GCM) and decrypts after a successful login. The password is
NEVER shipped — only this ciphertext, useless without the password.

Note: Python's "AES-GCM" ciphertext = IV||ciphertext||tag. To match
WebCrypto (which wants ciphertext||tag separately), this script strips the
16-byte tag from the end and stores ct = ciphertext-without-tag, iv = 12B.

Usage:  python3 encrypt_private.py [--pw-env BOOKSHELF_ADMIN_PW]
"""

import argparse
import base64
import json
import os
import sys

# PBKDF2 iterations must match the browser side exactly.
ITERATIONS = 310000
SALT_LEN = 16
IV_LEN = 12

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit(
        "ERROR: 'cryptography' package missing. Install with: pip install cryptography"
    )


def derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 — same params as WebCrypto deriveBits in the browser."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _pw_from_api_server():
    """Read BOOKSHELF_ADMIN_PW from the running api_server's /proc environ."""
    import glob
    import subprocess

    # Find the api_server pid (port or cmdline match)
    for env_path in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(env_path, "rb") as f:
                data = f.read()
        except (PermissionError, FileNotFoundError):
            continue
        if b"BOOKSHELF_ADMIN_PW" not in data:
            continue
        # environ holds exec-time env only — api_server.py's cmdline is NOT
        # in it. Match the service uniquely via BOOKSHELF_PORT=8770 instead.
        if b"BOOKSHELF_PORT=8770" not in data:
            continue
        for entry in data.split(b"\x00"):
            if entry.startswith(b"BOOKSHELF_ADMIN_PW="):
                return entry.split(b"=", 1)[1].decode("utf-8", "replace").strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="Encrypt books-private.json")
    parser.add_argument(
        "--src",
        default=os.path.expanduser("~/repos/books/data/books-private.json"),
    )
    parser.add_argument(
        "--out",
        default=os.path.expanduser("~/repos/books/data/books-private.json.enc"),
    )
    parser.add_argument("--pw-env", default="BOOKSHELF_ADMIN_PW")
    parser.add_argument("--pw-file", default=None, help="File containing the password")
    args = parser.parse_args()

    # Password: explicit file > env var > running api_server's environ.
    # Avoid argv (visible in `ps`). The /proc fallback keeps the cron watcher
    # self-sufficient: the api_server (same user) already holds BOOKSHELF_ADMIN_PW.
    password = None
    if args.pw_file and os.path.isfile(args.pw_file):
        with open(args.pw_file, "r", encoding="utf-8") as f:
            password = f.read().strip()
    if not password:
        password = os.environ.get(args.pw_env, "").strip()
    if not password:
        password = _pw_from_api_server()
    if not password:
        sys.exit(
            f"ERROR: no password. Set ${args.pw_env} or pass --pw-file <file>"
        )

    if not os.path.isfile(args.src):
        sys.exit(f"ERROR: source not found: {args.src}")

    with open(args.src, "r", encoding="utf-8") as f:
        plain = f.read()

    salt = os.urandom(SALT_LEN)
    iv = os.urandom(IV_LEN)
    key = derive_key(password, salt)

    # AESGCM.encrypt returns iv||ciphertext||tag on some configs — actually the
    # `cryptography` AESGCM.encrypt(nonce, data, None) returns ciphertext||tag
    # (16-byte tag appended). WebCrypto decrypt expects ciphertext||tag too, so
    # we can pass them joined or split. Store separately for explicitness.
    ct_with_tag = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)
    tag = ct_with_tag[-16:]
    ct = ct_with_tag[:-16]

    artifact = {
        "v": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ct": base64.b64encode(ct).decode(),
        "tag": base64.b64encode(tag).decode(),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(artifact, f, separators=(",", ":"))

    size = os.path.getsize(args.out)
    print(f"Encrypted {len(plain)} bytes -> {args.out} ({size} bytes)")
    print(f"  salt: {artifact['salt'][:12]}... iv: {artifact['iv'][:8]}...")
    print("Deploy this file alongside books.json — it's useless without the password.")


if __name__ == "__main__":
    main()