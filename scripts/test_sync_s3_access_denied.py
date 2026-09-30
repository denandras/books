#!/usr/bin/env python3
"""
Regression test for AccessDenied handling in sync_s3.py.

Verifies that when S3 PutObject (upload_file) raises a ClientError with
error code AccessDenied, the script:
  1. Catches the error gracefully (no unhandled exception)
  2. Returns False from upload_file()
  3. Logs an actionable error message
  4. sync_to_s3() continues processing other files and exits with errors

Also verifies that other ClientError codes (NoSuchBucket, InvalidAccessKeyId,
SignatureDoesNotMatch) and connection errors are handled gracefully.
"""

import os
import sys
import json
import tempfile
import logging
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    EndpointConnectionError,
    ConnectionClosedError,
)

# ─── Module loading ──────────────────────────────────────────────
# sync_s3.py calls _get_secret() at import time to read ~/.mc/config.json.
# We must mock that before importing the module.

_MOCK_CONFIG = {
    "aliases": {
        "tb1": {
            "url": "http://127.0.0.1:9010",
            "accessKey": "tb160cd7086",
            "secretKey": "mock-secret-key-for-testing",
            "api": "S3v4",
        }
    }
}


def _load_sync_s3():
    """Import sync_s3 with mocked _get_secret so it doesn't need real creds."""
    # We patch open() to return our mock config when reading ~/.mc/config.json
    mock_open = MagicMock(side_effect=lambda f, *a, **kw: tempfile.NamedTemporaryFile(
        mode='w+', suffix='.json', delete=False
    ) if 'config.json' in str(f) else open(f, *a, **kw))

    # Simpler: use sys.modules manipulation
    import importlib
    import types

    # Create a mock config file
    config_fd, config_path = tempfile.mkstemp(suffix=".json", prefix="mc_config_")
    with os.fdopen(config_fd, 'w') as f:
        json.dump(_MOCK_CONFIG, f)

    # Patch expanduser to point ~/.mc/config.json to our temp file
    original_expanduser = os.path.expanduser

    def patched_expanduser(path):
        if path == "~/.mc/config.json":
            return config_path
        return original_expanduser(path)

    with patch('os.path.expanduser', side_effect=patched_expanduser):
        # Remove any previously loaded module
        if 'sync_s3' in sys.modules:
            del sys.modules['sync_s3']
        # Add the scripts directory to path
        scripts_dir = os.path.expanduser("~/repos/books/scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        mod = importlib.import_module('sync_s3')
        return mod


sync_s3 = _load_sync_s3()


# ─── Helpers ─────────────────────────────────────────────────────

def make_access_denied_error(key="test/file.jpg"):
    """Create a ClientError that simulates S3 AccessDenied."""
    return ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "Access Denied",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 403,
            },
        },
        "PutObject",
    )


def make_client_error(code, message, operation="PutObject"):
    """Create a generic ClientError with a specific error code."""
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": message,
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 400,
            },
        },
        operation,
    )


def make_mock_s3_client(raise_error=None):
    """Create a mock S3 client whose upload_file raises the given error."""
    client = MagicMock()
    if raise_error is not None:
        client.upload_file.side_effect = raise_error
    # Mock paginator for list_existing_keys
    paginator = MagicMock()
    paginator.paginate.return_value = []
    client.get_paginator.return_value = paginator
    return client


def make_temp_file(content=b"test content", suffix=".jpg"):
    """Create a temporary file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, 'wb') as f:
        f.write(content)
    return path


# ─── Tests: upload_file with AccessDenied ────────────────────────

class TestAccessDeniedHandling:
    """Test that upload_file handles AccessDenied gracefully."""

    def test_access_denied_returns_false(self):
        """upload_file() returns False (not raises) on AccessDenied."""
        s3 = make_mock_s3_client(raise_error=make_access_denied_error())
        local_path = make_temp_file()

        try:
            result = sync_s3.upload_file(s3, local_path, "test/file.jpg")
            assert result is False, "upload_file should return False on AccessDenied"
        finally:
            os.unlink(local_path)

    def test_access_denied_no_unhandled_exception(self):
        """upload_file() does not raise an unhandled exception on AccessDenied."""
        s3 = make_mock_s3_client(raise_error=make_access_denied_error())
        local_path = make_temp_file()

        try:
            # This should not raise any exception
            result = sync_s3.upload_file(s3, local_path, "test/file.jpg")
        except Exception as e:
            pytest.fail(f"upload_file raised unhandled exception: {e}")
        finally:
            os.unlink(local_path)

    def test_access_denied_logs_error(self, caplog):
        """upload_file() logs an error message on AccessDenied."""
        s3 = make_mock_s3_client(raise_error=make_access_denied_error())
        local_path = make_temp_file()

        try:
            with caplog.at_level(logging.ERROR, logger="sync_s3"):
                sync_s3.upload_file(s3, local_path, "test/file.jpg")

            error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
            assert len(error_records) > 0, "Should log at least one ERROR on AccessDenied"
            # The error message should mention AccessDenied
            assert any("AccessDenied" in r.getMessage() for r in error_records), \
                "Error log should mention 'AccessDenied'"
        finally:
            os.unlink(local_path)

    def test_access_denied_does_not_crash_sync(self):
        """sync_to_s3() continues processing after AccessDenied on one file."""
        # Create a temp directory with files to sync
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a root file (index.html)
            with open(os.path.join(tmpdir, "index.html"), 'w') as f:
                f.write("<html>test</html>")

            # Create covers directory with a file
            os.makedirs(os.path.join(tmpdir, "covers"))
            with open(os.path.join(tmpdir, "covers", "book1.jpg"), 'wb') as f:
                f.write(b"fake jpeg")

            # Mock the S3 client: first upload gets AccessDenied, second succeeds
            s3 = make_mock_s3_client()
            s3.upload_file.side_effect = [make_access_denied_error(), None]
            # Mock list_existing_keys to return empty (no stale cleanup)
            paginator = MagicMock()
            paginator.paginate.return_value = []
            s3.get_paginator.return_value = paginator

            with patch.object(sync_s3, 'get_s3_client', return_value=s3):
                with patch.object(sync_s3, 'ROOT_FILES', ['index.html']):
                    with patch.object(sync_s3, 'SYNC_DIRS', ['covers']):
                        # sync_to_s3 should complete without raising
                        ok = sync_s3.sync_to_s3(tmpdir, dry_run=False)
                        # Should return False because there was 1 error
                        assert ok is False, \
                            "sync_to_s3 should return False when errors occur"

    def test_access_denied_succeeds_when_all_uploads_ok(self):
        """sync_to_s3() returns True when all uploads succeed (no AccessDenied)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "books.json"), 'w') as f:
                f.write('[]')

            s3 = make_mock_s3_client()
            paginator = MagicMock()
            paginator.paginate.return_value = []
            s3.get_paginator.return_value = paginator

            with patch.object(sync_s3, 'get_s3_client', return_value=s3):
                with patch.object(sync_s3, 'ROOT_FILES', ['books.json']):
                    with patch.object(sync_s3, 'SYNC_DIRS', []):
                        ok = sync_s3.sync_to_s3(tmpdir, dry_run=False)
                        assert ok is True, \
                            "sync_to_s3 should return True when no errors"


# ─── Tests: other ClientError codes ──────────────────────────────

class TestOtherClientErrors:
    """Test that other S3 error codes are also handled gracefully."""

    @pytest.mark.parametrize("error_code,message", [
        ("NoSuchBucket", "The specified bucket does not exist"),
        ("InvalidAccessKeyId", "The AWS Access Key Id you provided does not exist"),
        ("SignatureDoesNotMatch", "The request signature we calculated does not match"),
        ("SlowDown", "Reduce your request rate"),
        ("Unknown", "Generic error"),
    ])
    def test_client_error_returns_false(self, error_code, message):
        """upload_file() returns False on various ClientError codes."""
        s3 = make_mock_s3_client(
            raise_error=make_client_error(error_code, message)
        )
        local_path = make_temp_file()

        try:
            result = sync_s3.upload_file(s3, local_path, "test/file.jpg")
            assert result is False, \
                f"upload_file should return False on {error_code}"
        except Exception as e:
            pytest.fail(f"upload_file raised unhandled exception on {error_code}: {e}")
        finally:
            os.unlink(local_path)


# ─── Tests: connection errors ────────────────────────────────────

class TestConnectionErrors:
    """Test that connection-related exceptions are handled gracefully."""

    @pytest.mark.parametrize("exception", [
        NoCredentialsError(),
        EndpointConnectionError(endpoint_url="http://127.0.0.1:9010"),
        ConnectionClosedError(endpoint_url="http://127.0.0.1:9010"),
    ])
    def test_connection_error_returns_false(self, exception):
        """upload_file() returns False on connection errors."""
        s3 = make_mock_s3_client(raise_error=exception)
        local_path = make_temp_file()

        try:
            result = sync_s3.upload_file(s3, local_path, "test/file.jpg")
            assert result is False, \
                f"upload_file should return False on {type(exception).__name__}"
        except Exception as e:
            pytest.fail(
                f"upload_file raised unhandled exception on {type(exception).__name__}: {e}"
            )
        finally:
            os.unlink(local_path)


# ─── Tests: sync_to_s3 exit code behavior ────────────────────────

class TestSyncExitCode:
    """Test that sync_to_s3 returns the correct success/failure status."""

    def test_all_uploads_denied_returns_false(self):
        """sync_to_s3 returns False when all uploads get AccessDenied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "books.json"), 'w') as f:
                f.write('[]')

            s3 = make_mock_s3_client(raise_error=make_access_denied_error())
            paginator = MagicMock()
            paginator.paginate.return_value = []
            s3.get_paginator.return_value = paginator

            with patch.object(sync_s3, 'get_s3_client', return_value=s3):
                with patch.object(sync_s3, 'ROOT_FILES', ['books.json']):
                    with patch.object(sync_s3, 'SYNC_DIRS', []):
                        ok = sync_s3.sync_to_s3(tmpdir, dry_run=False)
                        assert ok is False

    def test_dry_run_does_not_trigger_s3(self):
        """dry_run=True should not call upload_file at all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "books.json"), 'w') as f:
                f.write('[]')

            s3 = MagicMock()

            with patch.object(sync_s3, 'get_s3_client', return_value=s3):
                with patch.object(sync_s3, 'ROOT_FILES', ['books.json']):
                    with patch.object(sync_s3, 'SYNC_DIRS', []):
                        ok = sync_s3.sync_to_s3(tmpdir, dry_run=True)
                        assert ok is True
                        # S3 upload_file should never be called in dry-run
                        s3.upload_file.assert_not_called()


# ─── Tests: unexpected exceptions ────────────────────────────────

class TestUnexpectedErrors:
    """Test that truly unexpected exceptions are caught and don't crash."""

    def test_unexpected_exception_returns_false(self):
        """upload_file() catches unexpected exceptions and returns False."""
        s3 = make_mock_s3_client(raise_error=RuntimeError("unexpected boom"))
        local_path = make_temp_file()

        try:
            result = sync_s3.upload_file(s3, local_path, "test/file.jpg")
            assert result is False, \
                "upload_file should return False on unexpected exception"
        except Exception as e:
            pytest.fail(f"upload_file raised unhandled exception: {e}")
        finally:
            os.unlink(local_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])