#!/usr/bin/env python3
"""
Sync book data + covers to S3 (tb1 bucket).
Uses mc (MinIO client) configured for tb1-s3.

Usage:
    python3 sync_s3.py [--local ~/repos/books] [--bucket tb1/books]
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

LOCAL_BASE = os.path.expanduser("~/repos/books")
S3_BUCKET = "tb1/books"
MC_CMD = os.path.expanduser("~/minio/mc")


def run_mc(*args):
    """Run mc command."""
    cmd = [MC_CMD] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result


def sync_to_s3(local_base, bucket):
    """Sync data + covers to S3."""
    # Sync data files
    data_dir = os.path.join(local_base, "data")
    if os.path.isdir(data_dir):
        print(f"Syncing {data_dir} → s3://{bucket}/data/")
        r = run_mc("cp", "--overwrite", f"{data_dir}/", f"s3://{bucket}/data/")
        if r.returncode != 0:
            print(f"  ERROR: {r.stderr}")
        else:
            print("  OK")

    # Sync public covers only (private covers are gitignored and excluded)
    covers_dir = os.path.join(local_base, "covers")
    if os.path.isdir(covers_dir):
        print(f"Syncing {covers_dir} → s3://{bucket}/covers/")
        r = run_mc("cp", "--overwrite", "--recursive", f"{covers_dir}/", f"s3://{bucket}/covers/")
        if r.returncode != 0:
            print(f"  ERROR: {r.stderr}")
        else:
            print("  OK")

    # Sync index.html, app.js, styles.css
    for f in ["index.html", "app.js", "styles.css"]:
        fpath = os.path.join(local_base, f)
        if os.path.exists(fpath):
            print(f"Syncing {f} → s3://{bucket}/{f}")
            r = run_mc("cp", "--overwrite", fpath, f"s3://{bucket}/{f}")
            if r.returncode != 0:
                print(f"  ERROR: {r.stderr}")
            else:
                print("  OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync book data to S3")
    parser.add_argument("--local", default=LOCAL_BASE, help="Local base dir")
    parser.add_argument("--bucket", default=S3_BUCKET, help="S3 bucket path")
    args = parser.parse_args()

    sync_to_s3(args.local, args.bucket)