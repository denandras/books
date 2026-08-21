#!/usr/bin/env python3
"""
Sync book data + covers to S3 (tb1 bucket on MinIO port 9010).
Uses boto3 directly — mc CLI has issues with spaces in S3 key paths.

Target path: tb1/documents/009 Egyéb/Books/

Usage:
    python3 sync_s3.py [--local ~/repos/books]
"""

import os
import sys
import argparse
from pathlib import Path

import boto3
from botocore.config import Config

# ─── Config ────────────────────────────────────────────────────────

LOCAL_BASE = os.path.expanduser("~/repos/books")
S3_BUCKET = "tb1"
S3_PREFIX = "documents/009 Egyéb/Books/"

# Credentials from ~/.mc/config.json (tb1 alias)
S3_ENDPOINT = "http://127.0.0.1:9010"
S3_ACCESS_KEY = "tb160cd7086"


def _get_secret():
    """Read secret key from ~/.mc/config.json."""
    import json
    with open(os.path.expanduser("~/.mc/config.json")) as f:
        cfg = json.load(f)
    return cfg["aliases"]["tb1"]["secretKey"]


S3_SECRET_KEY = _get_secret()

# Files to sync at repo root level
ROOT_FILES = ["index.html", "books.json"]
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


def upload_file(s3, local_path, s3_key, dry_run=False):
    """Upload a single file to S3."""
    if dry_run:
        print(f"  [DRY] {s3_key}")
        return True
    try:
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

        s3.upload_file(
            local_path,
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": content_type},
        )
        return True
    except Exception as e:
        print(f"  ERROR uploading {s3_key}: {e}")
        return False


def sync_to_s3(local_base, dry_run=False):
    """Sync repo content to S3."""
    s3 = get_s3_client()
    s3_prefix = S3_PREFIX

    uploaded = 0
    errors = 0

    # Get existing keys for cleanup
    existing_keys = list_existing_keys(s3, s3_prefix) if not dry_run else set()

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
                s3.delete_objects(
                    Bucket=S3_BUCKET,
                    Delete={"Objects": [{"Key": k} for k in batch]},
                )
                for k in batch:
                    print(f"    DEL {k}")

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
