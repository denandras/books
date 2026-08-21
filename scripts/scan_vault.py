#!/usr/bin/env python3
"""
Scan Obsidian vault for books (type: könyv), parse frontmatter + body,
download covers, write books.json (public) and books-private.json (all).

Usage:
    python3 scan_vault.py [--vault ~/obsidian] [--out ~/repos/books/data]
"""

import os
import re
import json
import hashlib
import unicodedata
import urllib.request
import urllib.error
import time
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Local modules
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from cover_downloader import CoverDownloader
from md_to_html import BookMarkdownConverter

# --- Config ---
VAULT_PATH = os.path.expanduser("~/obsidian")
OUTPUT_DIR = os.path.expanduser("~/repos/books/data")
COVERS_DIR = os.path.expanduser("~/repos/books/covers")
SKIP_DIRS = {".obsidian", "Notion", "_templates", ".trash", ".git"}
COVER_TIMEOUT = 15
COVER_RETRIES = 2

# Book sections to extract from body
SECTIONS = ["Idegen szavak", "Említések", "Vélemény", "Idézetek", "ami megfogott benne"]


def parse_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return None, content

    end = content.find("\n---", 3)
    if end == -1:
        return None, content

    fm_text = content[3:end].strip()
    body = content[end + 4:]

    fm = {}
    current_key = None

    for line in fm_text.split("\n"):
        m = re.match(r"^(\w[\w\-]*)\s*:\s*(.*)$", line)
        if m and not line.startswith(" "):
            key = m.group(1)
            val = m.group(2).strip()
            current_key = key
            if val == "":
                fm[key] = []
            elif val.startswith("[") and val.endswith("]"):
                items = re.findall(r'"([^"]*)"', val)
                if not items:
                    items = [v.strip() for v in val[1:-1].split(",") if v.strip()]
                fm[key] = items
            elif val.startswith('"') and val.endswith('"'):
                fm[key] = val[1:-1]
            else:
                fm[key] = val
        elif line.strip().startswith("- ") and current_key:
            item = line.strip()[2:].strip()
            if item.startswith('"') and item.endswith('"'):
                item = item[1:-1]
            if current_key not in fm or not isinstance(fm[current_key], list):
                fm[current_key] = []
            fm[current_key].append(item)

    return fm, body


def parse_body_sections(body):
    """Extract sections from markdown body."""
    sections = {}

    # Split by ### headers
    parts = re.split(r"^###\s+(.+)$", body, flags=re.MULTILINE)

    # parts[0] is preamble, then alternating (header, content)
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""

        # Normalize section name
        section_key = header.lower().strip()
        sections[section_key] = content

    # Also extract inline fields like idegenszo::
    idegenszo_match = re.search(r"idegenszo::\s*(.+)", body)
    if idegenszo_match:
        sections["idegenszo_inline"] = idegenszo_match.group(1).strip()

    # Extract idézet:: quotes (standalone format)
    quote_matches = re.findall(r">\s*idézet::\s*(.+?)(?=\n>|\n\n|\Z)", body, re.DOTALL)
    if quote_matches:
        sections["idezetek_inline"] = [q.strip() for q in quote_matches]

    return sections


def normalize_filename(name):
    """Normalize filename: NFC unicode, safe chars."""
    name = unicodedata.normalize("NFC", name)
    # Keep Hungarian chars, replace unsafe
    safe = re.sub(r'[^\w\-.\u00C0-\u017F\s]', "_", name)
    safe = safe.strip().replace(" ", "_")
    return safe[:200]


def slugify(title):
    """Create ASCII URL-safe slug from title.

    Uses NFD decomposition + ASCII ignore to transliterate Hungarian
    accented characters (ű→u, é→e, etc.) for consistent filenames.
    """
    slug = unicodedata.normalize("NFD", title)
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:80]


def scan_vault(vault_path, output_dir, covers_dir):
    """Scan vault for books, write JSON output."""
    books = []
    skipped = 0
    cover_dl = CoverDownloader(covers_dir)
    md_conv = BookMarkdownConverter()

    for root, dirs, files in os.walk(vault_path):
        # Skip unwanted directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(root, fname)
            try:
                fm, body = parse_frontmatter(fpath)
                if not fm:
                    skipped += 1
                    continue

                type_val = fm.get("type", "")
                is_book = False
                if isinstance(type_val, list):
                    is_book = "könyv" in type_val
                elif isinstance(type_val, str):
                    is_book = "könyv" in type_val

                if not is_book:
                    skipped += 1
                    continue

                rel_path = os.path.relpath(fpath, vault_path)
                folder = rel_path.split("/")[0] if "/" in rel_path else "root"

                # dg-publish check
                dg = fm.get("dg-publish", "")
                dg_publish = dg == True or dg == "true" or dg == "True"

                # Parse author
                author = fm.get("author", "")
                if isinstance(author, list):
                    author = ", ".join(
                        [a.replace("[[", "").replace("]]", "") for a in author]
                    )
                elif isinstance(author, str):
                    author = author.replace("[[", "").replace("]]", "")

                # Parse status
                status = fm.get("status", "")
                if isinstance(status, list):
                    status_list = [s.strip() for s in status]
                elif isinstance(status, str) and status:
                    status_list = [status.strip()]
                else:
                    status_list = []

                # Parse title
                title = fm.get("title", fname.replace(".md", ""))
                if isinstance(title, list):
                    title = str(title[0]) if title else fname.replace(".md", "")

                # Generate book ID (slug)
                book_id = slugify(title)

                # Parse body sections (raw markdown, for backwards compat)
                sections = parse_body_sections(body)

                # Convert body to HTML
                html_result = md_conv.convert(body)
                html_content = html_result["html"]
                html_sections = html_result["sections"]

                # Build book object
                book = {
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "cover_url": fm.get("cover", ""),
                    "cover": None,  # filled below — relative path to covers/<slug>.<ext>
                    "isbn": str(fm.get("isbn", "")) if fm.get("isbn") else "",
                    "pages": fm.get("pages", ""),
                    "rating": fm.get("rating", ""),
                    "status": status_list,
                    "dg_publish": dg_publish,
                    "folder": folder,
                    "source_file": rel_path,
                    "sections": {
                        "quotes": sections.get("idézetek", ""),
                        "quotes_inline": sections.get("idezetek_inline", []),
                        "mentions": sections.get("említések", ""),
                        "opinion": sections.get("vélemény", ""),
                        "foreign_words": sections.get("idegen szavak", ""),
                        "foreign_words_inline": sections.get("idegenszo_inline", ""),
                        "what_i_liked": sections.get("ami megfogott benne", ""),
                    },
                    "html_content": html_content,
                    "html_sections": html_sections,
                    "has_content": any(
                        v for v in [
                            sections.get("idézetek", ""),
                            sections.get("idezetek_inline", []),
                            sections.get("említések", ""),
                            sections.get("vélemény", ""),
                            sections.get("idegen szavak", ""),
                        ]
                    ),
                }

                books.append(book)

            except Exception as e:
                print(f"  WARN: error parsing {fpath}: {e}")
                skipped += 1

    # Download / generate covers
    print(f"Processing covers for {len(books)} books...")
    for i, book in enumerate(books):
        cover_url = book.get("cover_url", "")
        cover_file = cover_dl.get_cover(
            book["id"], book["title"], book.get("author", ""), cover_url
        )
        if cover_file:
            book["cover"] = f"covers/{cover_file}"
        status = "OK" if cover_file else "FAIL"
        print(f"  [{i+1}/{len(books)}] {book['title'][:40]:40s} → {status}")

    # Sort by title
    books.sort(key=lambda b: str(b["title"]).lower())

    # Split public/private
    public_books = [b for b in books if b["dg_publish"]]
    private_books = books  # all books

    # Write output
    os.makedirs(output_dir, exist_ok=True)

    public_path = os.path.join(output_dir, "books.json")
    with open(public_path, "w", encoding="utf-8") as f:
        json.dump(
            {"books": public_books, "count": len(public_books), "updated": datetime.now().isoformat()},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nWrote {len(public_books)} public books → {public_path}")

    private_path = os.path.join(output_dir, "books-private.json")
    with open(private_path, "w", encoding="utf-8") as f:
        json.dump(
            {"books": private_books, "count": len(private_books), "updated": datetime.now().isoformat()},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Wrote {len(private_books)} total books → {private_path}")

    # Summary
    print(f"\n=== Summary ===")
    print(f"Total books: {len(books)}")
    print(f"Published: {len(public_books)}")
    print(f"Unpublished: {len(private_books) - len(public_books)}")
    print(f"Lent: {sum(1 for b in books if 'lent' in b.get('status', []))}")
    print(f"With covers: {sum(1 for b in books if b['cover'])}")
    print(f"Without covers: {sum(1 for b in books if not b['cover'])}")
    print(f"With HTML content: {sum(1 for b in books if b.get('html_content'))}")
    print(f"Skipped (not books): {skipped}")

    return books


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan Obsidian vault for books")
    parser.add_argument("--vault", default=VAULT_PATH, help="Vault path")
    parser.add_argument("--out", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--covers", default=COVERS_DIR, help="Covers directory")
    parser.add_argument("--skip-covers", action="store_true", help="Skip cover download")
    args = parser.parse_args()

    if args.skip_covers:
        # Monkey-patch CoverDownloader to skip downloads
        CoverDownloader.get_cover = lambda self, bid, title, author, url: None

    scan_vault(args.vault, args.out, args.covers)