#!/usr/bin/env python3
"""
Book Shelf Watcher — scans Obsidian vault for books (type: könyv),
parses frontmatter + body, downloads covers, generates books.json.

Usage:
    python3 watcher.py [--vault ~/obsidian] [--out ~/repos/books]
    python3 watcher.py --skip-covers

Designed to run every 5 minutes via cron or as a daemon.
"""

import os
import re
import sys
import json
import time
import argparse
import importlib
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime

# ─── Config ────────────────────────────────────────────────────────
VAULT_PATH = os.path.expanduser("~/obsidian")
REPO_DIR = os.path.expanduser("~/repos/books")
COVERS_DIR = os.path.join(REPO_DIR, "covers")
OUTPUT_FILE = os.path.join(REPO_DIR, "books.json")

SKIP_DIRS = {".obsidian", "Notion", "_templates", ".trash", ".git", "node_modules"}

COVER_TIMEOUT = 15
COVER_RETRIES = 2
MIN_IMAGE_BYTES = 200

# ─── Markdown to HTML ──────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    from md_to_html import markdown_to_html as _md_to_html
except ImportError:
    import html as _htmlmod

    def _md_to_html(md_text):
        """Minimal fallback: escape HTML, convert headers, bold, italic."""
        text = _htmlmod.escape(md_text)
        text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', text, flags=re.MULTILINE)
        text = re.sub(r'^- (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
        return text


def body_to_html(body):
    """Convert markdown body to HTML, stripping leading H1 title lines."""
    # Remove all leading H1 lines (they duplicate the title field, or are nav links)
    while True:
        body = re.sub(r'^#\s+.+\n?', '', body, count=1)
        # Check if the next line is also H1
        if not re.match(r'^#\s+', body):
            break
    return _md_to_html(body) if body.strip() else ""


# ─── Frontmatter parsing ───────────────────────────────────────────

def parse_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file.
    Returns (frontmatter_dict, body_text) or (None, content) if no frontmatter.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return None, content

    end = content.find("\n---", 3)
    if end == -1:
        return None, content

    fm_text = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")

    fm = {}
    current_key = None

    for line in fm_text.split("\n"):
        # Key: value line (not indented)
        m = re.match(r"^(\w[\w\-]*)\s*:\s*(.*)$", line)
        if m and not line.startswith(" "):
            key = m.group(1)
            val = m.group(2).strip()
            current_key = key

            if val == "":
                # Multi-line value (list will be built from following - entries)
                fm[key] = []
            elif val.startswith("[") and val.endswith("]"):
                # Inline list: type: ["könyv"] or status: ["have read"]
                items = re.findall(r'"([^"]*)"', val)
                if not items:
                    items = [v.strip() for v in val[1:-1].split(",") if v.strip()]
                fm[key] = items
            elif val.startswith('"') and val.endswith('"'):
                fm[key] = val[1:-1]
            else:
                fm[key] = val
        elif line.strip().startswith("- ") and current_key:
            # List item under a multi-line key
            item = line.strip()[2:].strip()
            if item.startswith('"') and item.endswith('"'):
                item = item[1:-1]
            # Only append if the key was initialized as an empty list (multi-line value).
            # If the key already has a string value (e.g. title: "Watt" followed by
            # a stray indented list item), don't overwrite it.
            if isinstance(fm.get(current_key), list):
                fm[current_key].append(item)

    return fm, body


# ─── Field extraction helpers ──────────────────────────────────────

def is_book_type(fm):
    """Check if frontmatter type field indicates a book (könyv)."""
    type_val = fm.get("type", "")
    if isinstance(type_val, list):
        return "könyv" in type_val
    elif isinstance(type_val, str):
        return "könyv" in type_val
    return False


def extract_author(fm):
    """Extract author string from frontmatter, strip wikilinks."""
    author = fm.get("author", "")
    if isinstance(author, list):
        parts = []
        for a in author:
            a = str(a).replace("[[", "").replace("]]", "").strip()
            if a:
                parts.append(a)
        return ", ".join(parts)
    elif isinstance(author, str):
        return author.replace("[[", "").replace("]]", "").strip()
    return ""


def extract_published(fm):
    """Extract published boolean from dg-publish field."""
    dg = fm.get("dg-publish", "")
    if dg is True or dg == "true" or dg == "True":
        return True
    return False


def extract_lent(fm):
    """Extract lent boolean from status field (contains 'lent')."""
    status = fm.get("status", "")
    if isinstance(status, list):
        return "lent" in [s.strip().lower() for s in status]
    elif isinstance(status, str):
        return "lent" in status.lower()
    return False


def extract_title(fm, filename):
    """Extract title from frontmatter, fall back to filename."""
    title = fm.get("title", "")
    if isinstance(title, list):
        title = str(title[0]) if title else ""
    elif title is True or title is False:
        title = ""
    title = str(title).strip() if title else ""
    if not title:
        # Fall back to filename without .md
        title = filename.rsplit(".md", 1)[0]
    return title


def slugify_filename(filename):
    """Create URL-safe slug from filename (without .md extension)."""
    base = filename.rsplit(".md", 1)[0]
    # NFC normalize (important for S3 / Unicode consistency)
    base = unicodedata.normalize("NFC", base)
    slug = base.strip().lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove non-alphanumeric (keep hyphens, Unicode letters via \w)
    slug = re.sub(r"[^\w\-]", "", slug, flags=re.UNICODE)
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Trim hyphens
    slug = slug.strip("-")
    return slug[:80] if slug else "untitled"


# ─── Cover download ────────────────────────────────────────────────

def determine_ext(url, data=None):
    """Determine image extension from URL or magic bytes."""
    url_lower = url.lower() if url else ""
    if ".png" in url_lower:
        return ".png"
    if ".webp" in url_lower:
        return ".webp"
    if ".gif" in url_lower:
        return ".gif"
    if data:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
    return ".jpg"


def download_cover(url, book_id, covers_dir):
    """Download cover image, return relative path or None."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return None

    # Skip if already downloaded (check all extensions)
    for ext in (".jpg", ".png", ".webp", ".gif", ".jpeg"):
        existing = os.path.join(covers_dir, f"{book_id}{ext}")
        if os.path.isfile(existing) and os.path.getsize(existing) > 0:
            return f"covers/{book_id}{ext}"

    for attempt in range(COVER_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) BookShelf/1.0",
                    "Accept": "image/*",
                },
            )
            with urllib.request.urlopen(req, timeout=COVER_TIMEOUT) as resp:
                data = resp.read()

            if len(data) < MIN_IMAGE_BYTES:
                return None

            ext = determine_ext(url, data)
            dest_path = os.path.join(covers_dir, f"{book_id}{ext}")
            os.makedirs(covers_dir, exist_ok=True)

            with open(dest_path, "wb") as f:
                f.write(data)

            return f"covers/{book_id}{ext}"

        except Exception as e:
            if attempt < COVER_RETRIES:
                time.sleep(2)
            else:
                print(f"  WARN: cover download failed for {url}: {e}")
                return None

    return None


# ─── Main scan ─────────────────────────────────────────────────────

def scan_vault(vault_path, covers_dir, skip_covers=False):
    """Scan vault for books, download covers, return (books, skipped_count)."""
    books = []
    skipped = 0

    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(root, fname)

            try:
                fm, body = parse_frontmatter(fpath)
                if fm is None:
                    skipped += 1
                    continue

                if not is_book_type(fm):
                    skipped += 1
                    continue

                title = extract_title(fm, fname)
                author = extract_author(fm)
                published = extract_published(fm)
                lent = extract_lent(fm)
                book_id = slugify_filename(fname)
                cover_url = fm.get("cover", "")
                if isinstance(cover_url, list):
                    cover_url = str(cover_url[0]) if cover_url else ""
                cover_url = str(cover_url).strip() if cover_url else ""

                # Convert body markdown to HTML (strips H1 title)
                content_html = body_to_html(body)

                # Download cover (or use placeholder path)
                if skip_covers:
                    cover_path = f"covers/{book_id}.jpg"
                    status = "SKIP"
                else:
                    cover_path = download_cover(cover_url, book_id, covers_dir)
                    if cover_path:
                        status = "OK"
                    elif not cover_url:
                        status = "NONE"
                    else:
                        status = "FAIL"
                        cover_path = f"covers/{book_id}.jpg"  # expected path

                book = {
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "cover": cover_path,
                    "content": content_html,
                    "published": published,
                    "lent": lent,
                }

                print(f"  [{len(books)+1:3d}] {title[:50]:50s} cover={status}")
                books.append(book)

            except Exception as e:
                print(f"  WARN: error parsing {fpath}: {e}")
                skipped += 1

    return books, skipped


def generate_books_json(books, output_file):
    """Write books.json as a flat array."""
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(books)} books -> {output_file}")


def print_summary(books, skipped):
    """Print summary statistics."""
    published = sum(1 for b in books if b["published"])
    unpublished = len(books) - published
    lent = sum(1 for b in books if b["lent"])

    print(f"\n{'='*50}")
    print(f"  Total books:    {len(books)}")
    print(f"  Published:      {published}")
    print(f"  Unpublished:    {unpublished}")
    print(f"  Lent:           {lent}")
    print(f"  Skipped (not books): {skipped}")
    print(f"  Updated:        {datetime.now().isoformat()}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Book Shelf Watcher")
    parser.add_argument("--vault", default=VAULT_PATH, help="Obsidian vault path")
    parser.add_argument("--out", default=REPO_DIR, help="Output repo directory")
    parser.add_argument("--covers", default=None, help="Covers directory (default: <out>/covers)")
    parser.add_argument("--skip-covers", action="store_true", help="Skip cover downloads")
    args = parser.parse_args()

    vault_path = os.path.expanduser(args.vault)
    repo_dir = os.path.expanduser(args.out)
    covers_dir = args.covers or os.path.join(repo_dir, "covers")
    output_file = os.path.join(repo_dir, "books.json")

    print(f"[{datetime.now().isoformat()}] Scanning vault: {vault_path}")
    print(f"  Output: {output_file}")
    print(f"  Covers: {covers_dir}")
    if args.skip_covers:
        print("  (skipping cover downloads)")

    books, skipped = scan_vault(vault_path, covers_dir, skip_covers=args.skip_covers)

    # Sort by title (case-insensitive)
    books.sort(key=lambda b: str(b["title"]).lower())

    generate_books_json(books, output_file)
    print_summary(books, skipped)

    return books


if __name__ == "__main__":
    main()