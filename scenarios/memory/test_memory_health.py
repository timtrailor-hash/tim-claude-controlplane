"""
Memory health scenario tests.

Source: laptop memory MCP was broken for ~18 days (2026-03-20 to 2026-04-07).
"""
import os
import subprocess
from pathlib import Path

import pytest


class TestMemoryHealth:
    """Verify memory subsystem is functional."""

    def test_mem001_chroma_has_chunks(self):
        """MEM-001: ChromaDB must have >0 indexed chunks."""
        try:
            import chromadb
        except ImportError:
            pytest.skip("chromadb not installed")

        for candidate in [
            Path.home() / "code" / "memory_server_data" / "chroma",
            Path.home() / "code" / "memory_server" / "data" / "chroma",
        ]:
            if candidate.exists():
                client = chromadb.PersistentClient(path=str(candidate))
                coll = client.get_or_create_collection(
                    "conversations", metadata={"hnsw:space": "cosine"}
                )
                count = coll.count()
                assert count > 0, f"ChromaDB at {candidate} has 0 chunks"
                return
        pytest.fail("No ChromaDB directory found")

    def test_mem002_memory_server_importable(self):
        """MEM-002: memory_server.py must be importable."""
        import sys
        code_dir = str(Path.home() / "code")
        sys.path.insert(0, code_dir)
        try:
            import importlib
            spec = importlib.util.find_spec("memory_server")
            assert spec is not None, f"memory_server not found in {code_dir}"
        finally:
            sys.path.remove(code_dir)

    def test_mem003_health_check_script_works(self):
        """MEM-003: memory_health_check.sh --verbose must return OK."""
        script = Path.home() / ".claude" / "hooks" / "memory_health_check.sh"
        if not script.exists():
            pytest.skip("memory_health_check.sh not found")
        proc = subprocess.run(
            ["bash", str(script), "--verbose"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "OK:" in proc.stdout or "OK:" in proc.stderr, \
            f"Memory health check failed: {proc.stdout} {proc.stderr}"
