"""
tests/test_retriever_generator.py
Verify Phase 7: Retriever and Generator

- Retriever: Combines VectorStore and Embedder to retrieve top chunks.
- Generator: Extracts citations correctly and structures the prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rag_pipeline.core.generator import _extract_citations, _build_prompt, build_prompt_preview, Citation
from rag_pipeline.core.retriever import Retriever


def test_generator_citations():
    chunks = [
        {'chunk_id': 'c1', 'text': 'Acme Corp invoiced 5000 USD in January.', 'metadata': {'file_name': 'inv001.pdf', 'location_label': 'invoice INV-001', 'source_type': 'invoice', 'sender': 'Acme Corp'}},
        {'chunk_id': 'c2', 'text': 'Q4 revenue grew 15 percent year-over-year.', 'metadata': {'file_name': 'q4_report.pdf', 'location_label': 'page 3 of 20', 'source_type': 'pdf', 'sender': 'Finance Dept'}},
        {'chunk_id': 'c3', 'text': 'Beta Ltd has an outstanding balance of 12500 USD.', 'metadata': {'file_name': 'ledger.xlsx', 'location_label': 'sheet: Payments', 'source_type': 'excel', 'sender': None}},
    ]

    answer = 'Acme Corp invoiced USD 5,000 in January [1]. Revenue grew 15% in Q4 [2]. Beta Ltd owes USD 12,500 [3]. Both Acme and Beta are active vendors [1][3].'
    citations = _extract_citations(answer, chunks)

    assert len(citations) == 3, f"Expected 3 citations, got {len(citations)}"
    assert citations[0].file_name == 'inv001.pdf'
    assert citations[1].file_name == 'q4_report.pdf'
    assert citations[2].file_name == 'ledger.xlsx'
    
    # Check bounds safety
    answer_with_bad_ref = 'See source [1] and also [99] for details.'
    citations2 = _extract_citations(answer_with_bad_ref, chunks)
    assert len(citations2) == 1
    assert citations2[0].index == 1

def test_generator_prompt():
    chunks = [
        {'chunk_id': 'c1', 'text': 'Acme Corp invoiced 5000 USD in January.', 'metadata': {'file_name': 'inv001.pdf', 'location_label': 'invoice INV-001', 'source_type': 'invoice', 'sender': 'Acme Corp'}},
    ]
    prompt = _build_prompt('What is the total invoice?', chunks)
    assert '[1] inv001.pdf | invoice INV-001 | (Acme Corp) | [invoice]' in prompt
    assert 'QUESTION: What is the total invoice?' in prompt

class DummyEmbedder:
    def encode_single(self, text):
        return [0.1, 0.2, 0.3]

class DummyVectorStore:
    def query(self, embedding, top_k=5, filters=None):
        return [
            {"chunk_id": "c1", "text": "Result 1", "score": 0.9, "metadata": {}},
            {"chunk_id": "c2", "text": "Result 2", "score": 0.8, "metadata": {}},
        ]

def test_retriever():
    retriever = Retriever(vector_store=DummyVectorStore(), embedder=DummyEmbedder(), use_bm25=False)
    results = retriever.retrieve("Hello")
    assert len(results) == 2
    assert results[0]["chunk_id"] == "c1"

def run_all():
    print("Running Generator Tests...")
    test_generator_citations()
    test_generator_prompt()
    print("[PASS] Generator Tests")
    
    print("Running Retriever Tests...")
    test_retriever()
    print("[PASS] Retriever Tests")

if __name__ == "__main__":
    run_all()
