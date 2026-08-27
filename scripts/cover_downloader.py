#!/usr/bin/env python3
"""
Cover downloader for the book shelf.

Two modes:
1. Download: if a cover URL is present in frontmatter, fetch the image
   to covers/<slug>.<ext>.
2. Placeholder: if no URL (or download failed), generate a simple colored
   cover with the book title rendered via Pillow.

Usage:
    from cover_downloader import CoverDownloader
    cd = CoverDownloader(covers_dir="/path/to/covers")
    path = cd.get_cover(book_id, title, author, cover_url)
"""

import os
import re
import time
import urllib.request
import urllib.error
import hashlib
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

COVER_TIMEOUT = 15
COVER_RETRIES = 2
MIN_IMAGE_BYTES = 200

# Palette for placeholder covers — warm, muted tones (no red)
PALETTE = [
    (44, 62, 80),    # dark blue-grey
    (52, 73, 94),    # slate
    (39, 55, 70),    # deep teal
    (93, 109, 126),  # grey-blue
    (26, 82, 118),   # ocean blue
    (36, 48, 61),    # charcoal blue
    (72, 96, 117),   # steel blue
    (33, 47, 61),    # midnight
]


def _determine_ext(url, data=None):
    """Determine image extension from URL or magic bytes."""
    url_lower = url.lower() if url else ""
    if ".png" in url_lower:
        return ".png"
    if ".webp" in url_lower:
        return ".webp"
    if ".gif" in url_lower:
        return ".gif"
    if ".jpeg" in url_lower or ".jpg" in url_lower:
        return ".jpg"
    # Check magic bytes
    if data:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
    # Default
    return ".jpg"


def _download(url, dest_path):
    """Download a URL to a file. Returns True on success."""
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
                return False
            # Validate that downloaded data is actually an image (not HTML/etc)
            try:
                Image.open(BytesIO(data)).verify()
            except Exception:
                return False
            # Determine extension from actual data
            ext = _determine_ext(url, data)
            final_path = dest_path
            if not final_path.endswith(ext):
                base = os.path.splitext(dest_path)[0]
                final_path = base + ext
            with open(final_path, "wb") as f:
                f.write(data)
            # If we wrote to a different ext than the caller expected, rename
            if final_path != dest_path and os.path.exists(dest_path) and dest_path != final_path:
                os.remove(dest_path)
            return final_path
        except Exception as e:
            if attempt < COVER_RETRIES:
                time.sleep(2)
            else:
                return False
    return False


def _find_font(size):
    """Find a usable TTF font on the system, fall back to default."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for fp in font_paths:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_width):
    """Wrap text to fit within max_width using the given font."""
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _generate_placeholder(title, author, dest_path):
    """Generate a placeholder cover image with title text."""
    width, height = 400, 600

    # Deterministic color from title hash
    color_idx = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16) % len(PALETTE)
    bg_color = PALETTE[color_idx]

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Border frame
    margin = 20
    border_color = (
        max(bg_color[0] - 30, 0),
        max(bg_color[1] - 30, 0),
        max(bg_color[2] - 30, 0),
    )
    draw.rectangle(
        [margin, margin, width - margin, height - margin],
        outline=border_color,
        width=2,
    )

    # Title
    title_font = _find_font(28)
    title_lines = _wrap_text(draw, title, title_font, width - 2 * margin - 20)
    # Limit lines
    max_title_lines = 8
    if len(title_lines) > max_title_lines:
        title_lines = title_lines[:max_title_lines]
        title_lines[-1] = title_lines[-1][:30] + "…"

    line_height = 36
    total_h = len(title_lines) * line_height
    start_y = (height - total_h) // 2 - 30

    for i, line in enumerate(title_lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) // 2
        y = start_y + i * line_height
        # Light text color
        draw.text((x, y), line, fill=(240, 240, 240), font=title_font)

    # Author below
    if author:
        author_font = _find_font(16)
        author_short = author if len(author) <= 40 else author[:37] + "..."
        bbox = draw.textbbox((0, 0), author_short, font=author_font)
        author_w = bbox[2] - bbox[0]
        ax = (width - author_w) // 2
        ay = start_y + len(title_lines) * line_height + 20
        draw.text((ax, ay), author_short, fill=(200, 200, 200), font=author_font)

    # Small decorative line
    line_y = start_y + len(title_lines) * line_height + 60
    draw.line(
        [(width // 2 - 40, line_y), (width // 2 + 40, line_y)],
        fill=(180, 180, 180),
        width=1,
    )

    img.save(dest_path, "JPEG", quality=85)
    return dest_path


class CoverDownloader:
    """Download or generate book covers."""

    def __init__(self, covers_dir):
        self.covers_dir = covers_dir
        os.makedirs(covers_dir, exist_ok=True)

    def get_cover(self, book_id, title, author="", cover_url=""):
        """
        Get a cover for a book. Returns the relative filename (e.g. 'my-book.jpg')
        or None on total failure.

        1. If cover_url is a valid HTTP URL, try to download.
        2. If download fails or no URL, generate a placeholder.
        """
        # First check if any cover already exists for this book_id
        existing = self._find_existing(book_id)
        if existing:
            return existing

        # Try downloading from URL
        if cover_url and isinstance(cover_url, str) and cover_url.startswith("http"):
            # Try with .jpg first, _download will fix extension
            dest = os.path.join(self.covers_dir, f"{book_id}.jpg")
            result = _download(cover_url, dest)
            if result:
                return os.path.basename(result)

        # Generate placeholder
        placeholder_path = os.path.join(self.covers_dir, f"{book_id}.jpg")
        try:
            _generate_placeholder(title, author or "", placeholder_path)
            return os.path.basename(placeholder_path)
        except Exception as e:
            print(f"  WARN: placeholder generation failed for {title}: {e}")
            return None

    def _find_existing(self, book_id):
        """Check if a cover file already exists for the given book_id."""
        for ext in (".jpg", ".png", ".webp", ".gif", ".jpeg"):
            path = os.path.join(self.covers_dir, f"{book_id}{ext}")
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return os.path.basename(path)
        return None


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Download/generate book covers")
    parser.add_argument("--covers-dir", default="covers", help="Output directory")
    parser.add_argument("--id", required=True, help="Book ID/slug")
    parser.add_argument("--title", required=True, help="Book title")
    parser.add_argument("--author", default="", help="Book author")
    parser.add_argument("--url", default="", help="Cover URL")
    args = parser.parse_args()

    cd = CoverDownloader(args.covers_dir)
    result = cd.get_cover(args.id, args.title, args.author, args.url)
    if result:
        print(f"Cover: {result}")
    else:
        print("Failed to get cover")
        sys.exit(1)