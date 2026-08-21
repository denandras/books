#!/usr/bin/env python3
"""
Book Shelf API — lightweight auth + private book data server.
Serves books-private.json to authenticated admin users only.
Public books.json is served statically by Vercel.

Port: 8770
Endpoints:
  GET  /api/books          → public books (from books.json, no auth needed)
  GET  /api/books/:id      → single book detail (public or private w/ auth)
  POST /api/login           → password auth, sets HttpOnly cookie
  GET  /api/me              → check auth state
  GET  /api/covers/:id      → proxy cover image
  POST /api/scan            → trigger vault rescan (admin only)
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import http.server
import urllib.request
import urllib.error
from http.cookies import SimpleCookie
from pathlib import Path

# --- Config ---
PORT = int(os.environ.get("BOOKSHELF_PORT", "8770"))
DATA_DIR = os.path.expanduser("~/repos/books/data")
COVERS_DIR = os.path.expanduser("~/repos/books/covers")
SECRET = os.environ.get("BOOKSHELF_SECRET", secrets.token_hex(32))
COOKIE_NAME = "bookshelf_auth"
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # 10 years

# Admin password — stored as env var (plaintext for simplicity, behind reverse proxy)
ADMIN_PASSWORD = os.environ.get("BOOKSHELF_ADMIN_PW", "Kr4t0mGuy!*trombone")

# CORS — allow Vercel frontend
ALLOWED_ORIGINS = [
    "https://books.denandras.cloud",
    "http://localhost:4173",
    "http://localhost:8770",
]


def sign_token(payload: str) -> str:
    """HMAC-sign a token."""
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_token(token: str) -> bool:
    """Verify HMAC token. Token format: timestamp.signature."""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        ts_str, sig = parts
        expected_sig = sign_token(ts_str)
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def create_token() -> str:
    """Create auth token: timestamp.signature."""
    ts = str(int(time.time()))
    sig = sign_token(ts)
    return f"{ts}.{sig}"


def load_books_json(path):
    """Load a books JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"books": [], "count": 0, "updated": ""}


def get_book_by_id(book_id, include_private=False):
    """Find a book by ID in the JSON data."""
    data = load_books_json(os.path.join(DATA_DIR, "books.json"))
    for book in data.get("books", []):
        if book.get("id") == book_id:
            return book

    if include_private:
        private_data = load_books_json(os.path.join(DATA_DIR, "books-private.json"))
        for book in private_data.get("books", []):
            if book.get("id") == book_id:
                return book

    return None


class BookShelfHandler(http.server.BaseHTTPRequestHandler):
    def _set_cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Cookie")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, data, content_type="image/jpeg"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _get_cookie_token(self):
        """Extract auth token from cookie header."""
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        if morsel and verify_token(morsel.value):
            return morsel.value
        return None

    def _is_authenticated(self):
        return self._get_cookie_token() is not None

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # --- /api/me ---
        if path == "/api/me":
            self._send_json({"authenticated": self._is_authenticated()})
            return

        # --- /api/books ---
        if path == "/api/books":
            authed = self._is_authenticated()
            if authed:
                data = load_books_json(os.path.join(DATA_DIR, "books-private.json"))
            else:
                data = load_books_json(os.path.join(DATA_DIR, "books.json"))
            # Remove source_file for non-authenticated users
            if not authed:
                for book in data.get("books", []):
                    book.pop("source_file", None)
                    book.pop("folder", None)
            self._send_json(data)
            return

        # --- /api/books/:id ---
        if path.startswith("/api/books/"):
            book_id = path.split("/api/books/")[1].split("?")[0]
            authed = self._is_authenticated()
            book = get_book_by_id(book_id, include_private=authed)
            if not book:
                self._send_json({"error": "Book not found"}, 404)
                return
            if not book.get("dg_publish") and not authed:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            if not authed:
                book.pop("source_file", None)
                book.pop("folder", None)
            self._send_json(book)
            return

        # --- /api/covers/:id ---
        if path.startswith("/api/covers/"):
            book_id = path.split("/api/covers/")[1].split("?")[0]
            # Try to find cover file
            for ext in [".jpg", ".png", ".webp", ".gif"]:
                cover_path = os.path.join(COVERS_DIR, f"{book_id}{ext}")
                if os.path.exists(cover_path):
                    with open(cover_path, "rb") as f:
                        data = f.read()
                    ct = "image/jpeg"
                    if ext == ".png":
                        ct = "image/png"
                    elif ext == ".webp":
                        ct = "image/webp"
                    self._send_image(data, ct)
                    return
            self._send_json({"error": "Cover not found"}, 404)
            return

        # --- 404 ---
        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        # --- /api/login ---
        if path == "/api/login":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            password = data.get("password", "")
            if password == ADMIN_PASSWORD:
                token = create_token()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Set-Cookie",
                    f"{COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=None; Path=/; Max-Age={COOKIE_MAX_AGE}",
                )
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"authenticated": True}).encode())
            else:
                self._send_json({"error": "Invalid password"}, 401)
            return

        # --- /api/scan (admin only) ---
        if path == "/api/scan":
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized"}, 401)
                return
            # Trigger rescan
            try:
                import subprocess
                subprocess.Popen(
                    ["python3", os.path.expanduser("~/repos/books/scripts/scan_vault.py")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._send_json({"status": "Scan started"})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        # Minimal logging
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")


def main():
    server = http.server.HTTPServer(("127.0.0.1", PORT), BookShelfHandler)
    print(f"Book Shelf API running on http://127.0.0.1:{PORT}")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Covers dir: {COVERS_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()