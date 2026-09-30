#!/usr/bin/env python3
"""
Sync book data + covers to S3 (tb1 bucket on MinIO port 9010).
Uses boto3 directly — mc CLI has issues with spaces in S3 key paths.

Target path: tb1/documents/009 Egyéb/Books/

Usage:
    python3 sync_s3.py [--local ~/repos/books]
"""

import os
import re
import sys
import shutil
import logging
import unicodedata
import argparse
import tempfile
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    EndpointConnectionError,
    ConnectionClosedError,
)

# ─── Config ────────────────────────────────────────────────────────

LOCAL_BASE = os.path.expanduser("~/repos/books")
S3_BUCKET = "tb1"
S3_PREFIX = "documents/009 Egyéb/Books/"

# Credentials from ~/.mc/config.json (tb1 alias)
S3_ENDPOINT = "http://127.0.0.1:9010"
S3_ACCESS_KEY = "tb160cd7086"

# MinIO data directory — files here block S3 writes to same key (AccessDenied)
MINIO_DATA_DIR = "/mnt/tb2/tb1-data"

# Force single-PUT (put_object) for files under this size to avoid multipart
# upload AccessDenied. MinIO's multipart completion can fail with AccessDenied
# when the target key has a conflicting physical file in its data directory.
# 100 MB threshold means virtually all book assets go via single put_object.
MULTIPART_THRESHOLD = 100 * 1024 * 1024

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=MULTIPART_THRESHOLD,
    multipart_chunksize=MULTIPART_THRESHOLD,
)

# ─── Logging ───────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_s3")


def _get_secret():
    """Read secret key from ~/.mc/config.json."""
    import json
    with open(os.path.expanduser("~/.mc/config.json")) as f:
        cfg = json.load(f)
    return cfg["aliases"]["tb1"]["secretKey"]


S3_SECRET_KEY = _get_secret()
# Files to sync at repo root level
ROOT_FILES = ["index.html", "books.json"]
# Deployable artifact from data/ — ciphertext only, useless without the password.
# (data/books-private.json itself stays local-only.)
DATA_FILES = ["books-private.json.enc"]
# Directories to sync recursively
SYNC_DIRS = ["covers", "assets"]
# File patterns to exclude
EXCLUDE_PATTERNS = {".gitignore", ".gitignore.local", ".DS_Store"}


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


def list_existing_keys(s3, prefix):
    """List all existing object keys under prefix."""
    keys = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def normalize_s3_key(key):
    """Normalize an S3 key: NFC unicode + replace non-ASCII with '_'.

    MinIO can fail on non-ASCII characters (e.g. á, é) in key paths.
    We normalize to NFC then replace any remaining non-ASCII with '_'.
    """
    key = unicodedata.normalize("NFC", key)
    key = re.sub(r"[^\x00-\x7F]", "_", key)
    return key


def is_in_minio_data_dir(local_path):
    """Check if the source file is inside MinIO's data directory.

    Files physically present in MinIO's data dir cause AccessDenied on
    any S3 upload attempt to the same key — MinIO sees a conflict.
    """
    try:
        abs_path = os.path.abspath(local_path)
        minio_abs = os.path.abspath(MINIO_DATA_DIR)
        return abs_path.startswith(minio_abs + os.sep)
    except Exception:
        return False


def upload_file(s3, local_path, s3_key, dry_run=False):
    """Upload a single file to S3.

    Handles three known MinIO pitfalls:
    1. Source file inside MinIO data dir -> copy to /tmp first
    2. AccessDenied on multipart -> use put_object (high multipart threshold)
    3. Non-ASCII in S3 key causing AccessDenied -> retry with normalized key
    """
    if dry_run:
        print(f"  [DRY] {s3_key}")
        return True

    content_type = "application/octet-stream"
    if s3_key.endswith(".html"):
        content_type = "text/html; charset=utf-8"
    elif s3_key.endswith(".json"):
        content_type = "application/json; charset=utf-8"
    elif s3_key.endswith(".jpg") or s3_key.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif s3_key.endswith(".png"):
        content_type = "image/png"
    elif s3_key.endswith(".webp"):
        content_type = "image/webp"
    elif s3_key.endswith(".gif"):
        content_type = "image/gif"
    elif s3_key.endswith(".js"):
        content_type = "application/javascript"
    elif s3_key.endswith(".css"):
        content_type = "text/css"

    # If source is inside MinIO data dir, copy to /tmp first
    # (MinIO blocks S3 writes to keys that have a physical file in its data dir)
    temp_path = None
    upload_source = local_path
    if is_in_minio_data_dir(local_path):
        temp_dir = tempfile.mkdtemp(prefix="s3upload_")
        temp_path = os.path.join(temp_dir, os.path.basename(local_path))
        log.info("Source in MinIO data dir, copying to %s", temp_path)
        shutil.copy2(local_path, temp_path)
        upload_source = temp_path

    def _do_upload(key):
        s3.upload_file(
            upload_source,
            S3_BUCKET,
            key,
            ExtraArgs={"ContentType": content_type},
            Config=TRANSFER_CONFIG,
        )

    try:
        try:
            _do_upload(s3_key)
            log.info("Uploaded %s", s3_key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            # If AccessDenied and the key has non-ASCII, retry with normalized key
            if error_code == "AccessDenied":
                normalized = normalize_s3_key(s3_key)
                if normalized != s3_key:
                    log.warning(
                        "AccessDenied for %s — retrying with normalized key %s",
                        s3_key, normalized,
                    )
                    _do_upload(normalized)
                    log.info("Uploaded %s (normalized from %s)", normalized, s3_key)
                    return True
            raise
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "AccessDenied":
            hint = ""
            if is_in_minio_data_dir(local_path):
                hint = (
                    f" HINT: Source file is inside MinIO data dir ({MINIO_DATA_DIR})."
                    f" MinIO blocks S3 writes to keys with a physical file in its data directory."
                    f" The temp-copy workaround was attempted but may have failed."
                )
            log.error(
                "AccessDenied for s3://%s/%s: %s%s",
                S3_BUCKET, s3_key, e, hint,
            )
        elif error_code == "NoSuchBucket":
            log.error("NoSuchBucket: bucket '%s' does not exist", S3_BUCKET)
        elif error_code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
            log.error(
                "%s: credentials are invalid or misconfigured "
                "(check ~/.mc/config.json tb1 alias)",
                error_code,
            )
        else:
            log.error("ClientError [%s] uploading s3://%s/%s: %s", error_code, S3_BUCKET, s3_key, e)
        return False
    except (NoCredentialsError, EndpointConnectionError, ConnectionClosedError) as e:
        log.error("Connection error uploading %s: %s", s3_key, e)
        return False
    except Exception as e:
        log.error("Unexpected error uploading %s: %s", s3_key, e)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            shutil.rmtree(os.path.dirname(temp_path), ignore_errors=True)


def sync_to_s3(local_base, dry_run=False):
    """Sync repo content to S3."""
    try:
        s3 = get_s3_client()
    except (NoCredentialsError, EndpointConnectionError, ConnectionClosedError) as e:
        log.error("Cannot connect to S3: %s", e)
        log.error("Check that MinIO is running on %s and credentials are valid.", S3_ENDPOINT)
        return False
    except Exception as e:
        log.error("Failed to initialize S3 client: %s", e)
        return False
    s3_prefix = S3_PREFIX

    uploaded = 0
    errors = 0

    # Get existing keys for cleanup
    existing_keys = set()
    if not dry_run:
        try:
            existing_keys = list_existing_keys(s3, s3_prefix)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            log.warning("Could not list existing keys [%s]: %s — stale cleanup skipped", error_code, e)
        except (EndpointConnectionError, ConnectionClosedError) as e:
            log.warning("Connection lost while listing keys: %s — stale cleanup skipped", e)

    uploaded_keys = set()

    # Sync root files
    for fname in ROOT_FILES:
        fpath = os.path.join(local_base, fname)
        if os.path.isfile(fpath):
            s3_key = s3_prefix + fname
            print(f"  {fname} -> {s3_key}")
            if upload_file(s3, fpath, s3_key, dry_run):
                uploaded += 1
                uploaded_keys.add(s3_key)
            else:
                errors += 1

    # Sync deployable encrypted artifacts from data/
    for fname in DATA_FILES:
        fpath = os.path.join(local_base, "data", fname)
        if os.path.isfile(fpath):
            s3_key = s3_prefix + "data/" + fname
            print(f"  data/{fname} -> {s3_key}")
            if upload_file(s3, fpath, s3_key, dry_run):
                uploaded += 1
                uploaded_keys.add(s3_key)
            else:
                errors += 1
        else:
            log.warning("Encrypted artifact missing: %s — private notes will be unavailable after login", fpath)

    # Sync directories recursively
    for dirname in SYNC_DIRS:
        dirpath = os.path.join(local_base, dirname)
        if not os.path.isdir(dirpath):
            continue
        for root, dirs, files in os.walk(dirpath):
            for f in sorted(files):
                if f in EXCLUDE_PATTERNS:
                    continue
                fpath = os.path.join(root, f)
                rel_path = os.path.relpath(fpath, local_base)
                s3_key = s3_prefix + rel_path
                print(f"  {rel_path} -> {s3_key}")
                if upload_file(s3, fpath, s3_key, dry_run):
                    uploaded += 1
                    uploaded_keys.add(s3_key)
                else:
                    errors += 1

    # Delete stale objects (not in current upload set)
    if not dry_run and existing_keys:
        stale = existing_keys - uploaded_keys
        if stale:
            print(f"\n  Cleaning {len(stale)} stale objects...")
            stale_list = list(stale)
            for i in range(0, len(stale_list), 1000):
                batch = stale_list[i : i + 1000]
                try:
                    s3.delete_objects(
                        Bucket=S3_BUCKET,
                        Delete={"Objects": [{"Key": k} for k in batch]},
                    )
                    for k in batch:
                        log.info("Deleted stale object %s", k)
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "Unknown")
                    log.error("Failed to delete %d stale objects [%s]: %s", len(batch), error_code, e)
                    errors += 1
                except (EndpointConnectionError, ConnectionClosedError) as e:
                    log.error("Connection lost during stale cleanup: %s", e)
                    errors += 1

    print(f"\n  Uploaded: {uploaded}, Errors: {errors}")
    if not dry_run and existing_keys:
        print(f"  Stale deleted: {len(existing_keys - uploaded_keys)}")
    return errors == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync book data to S3 (MinIO)")
    parser.add_argument("--local", default=LOCAL_BASE, help="Local base dir")
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    args = parser.parse_args()

    local_base = os.path.expanduser(args.local)
    print(f"Syncing {local_base} -> s3://{S3_BUCKET}/{S3_PREFIX}")
    ok = sync_to_s3(local_base, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)