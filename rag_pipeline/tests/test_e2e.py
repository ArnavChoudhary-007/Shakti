"""
tests/test_e2e.py
End-to-End Pipeline Verification

Uses FastAPI TestClient to test the complete ingestion and retrieval pipeline.
Simulates uploading a file and then querying it.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient
from rag_pipeline.api.main import app

# We override the DB paths to use temp dirs so we don't mess up the actual DB
import rag_pipeline.api.main as api_main
import rag_pipeline.core.vectorstore as vs_module
import rag_pipeline.structured_db.db as db_module

client = TestClient(app)

def run_e2e():
    print("=== End-to-End API Pipeline Test ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override config paths for testing
        api_main._CONFIG["structured_db"] = {"sqlite_path": str(Path(tmpdir) / "test.db")}
        api_main._CONFIG["vector_store"] = {
            "backend": "faiss",
            "faiss_index_path": str(Path(tmpdir) / "index.faiss")
        }
        api_main._CONFIG["embedding"] = {"model_name": "all-MiniLM-L6-v2", "device": "cpu"}
        
        # Reset singletons
        api_main._vector_store = None
        api_main._struct_db = None
        
        # Create a sample email file
        sample_txt = Path(tmpdir) / "sample_e2e.eml"
        sample_txt.write_text("From: sender@example.com\nSubject: Project Update\n\nProject Apollo launched in 2024. The budget was $5M.")
        
        print(f"1. Ingesting {sample_txt.name}...")
        resp = client.post("/ingest_path", params={"path": str(sample_txt)})
        assert resp.status_code == 200, f"Ingest failed: {resp.text}"
        ingest_data = resp.json()
        print(f"   [PASS] Ingest OK: {ingest_data['doc_count']} docs, {ingest_data['chunk_count']} chunks")
        
        print("2. Checking /docs_count...")
        resp = client.get("/docs_count")
        assert resp.status_code == 200
        count_data = resp.json()
        assert count_data["chunk_count"] > 0
        print(f"   [PASS] Vector store has {count_data['chunk_count']} chunks")
        
        # Test Query (Mocking Generator to avoid downloading Ollama model in CI)
        print("3. Querying the pipeline...")
        class MockGenerator:
            def __init__(self, *args, **kwargs):
                self.default_text_model = "mock"
            async def generate(self, query, chunks, *args, **kwargs):
                from rag_pipeline.core.generator import CitationResult, Citation
                c = Citation(index=1, file_name=chunks[0]["metadata"]["file_name"], location="", snippet="", source_type="")
                return CitationResult(
                    answer=f"Mocked answer for {query} [1]",
                    citations=[c],
                    model="mock_model",
                    used_sql=False
                )
        api_main._generator = MockGenerator()
        
        resp = client.post("/query", json={"query": "What project launched in 2024?", "top_k": 3, "stream": False})
        assert resp.status_code == 200, f"Query failed: {resp.text}"
        query_data = resp.json()
        
        assert "Mocked answer" in query_data["answer"]
        assert len(query_data["citations"]) == 1
        assert query_data["citations"][0]["file_name"] == sample_txt.name
        print("   [PASS] Retrieval and generation pipeline works via API")
        
        print("\n[OK] E2E Pipeline verified.")

if __name__ == "__main__":
    run_e2e()
