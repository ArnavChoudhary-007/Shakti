"""
core/generator.py
Citation-grounded streaming generator via Ollama.

Pipeline:
  1. Number retrieved chunks [1]..[N] in the prompt with their metadata.
  2. Instruct the model to cite every claim with [N] markers.
  3. Stream the response token-by-token from Ollama.
  4. Post-process to extract which citation numbers were actually used.
  5. Return a CitationResult with the answer + structured citations list.

The citations list maps each [N] → {index, file_name, location, snippet}.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Citation types ────────────────────────────────────────────

@dataclass
class Citation:
    index: int            # the [N] number used in the answer text
    file_name: str        # e.g. "report.pdf"
    location: str         # e.g. "page 3 of 20" or "00:02:15–00:03:40"
    snippet: str          # first 200 chars of the source chunk
    source_type: str = ""
    sender: Optional[str] = None


@dataclass
class CitationResult:
    answer: str                          # full answer with [N] markers
    citations: List[Citation] = field(default_factory=list)
    model: str = ""
    used_sql: bool = False               # True if answer came from structured DB


# ── Prompt builder ────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise research assistant. Answer the user's question using ONLY \
the sources provided below. Every factual claim you make MUST be followed \
immediately by a citation in square brackets, e.g. [1] or [2].

Rules:
- Cite every statement. If two sources support the same claim, cite both: [1][2].
- If the answer is not in the provided sources, your response MUST BE EXACTLY:
  "not found in the documents"
  Do not explain, elaborate, or add any other words.
- Do NOT cite sources that are irrelevant to the claim.
- Do NOT make up information.
- Be concise and direct.
"""


def _build_prompt(query: str, chunks: List[Dict[str, Any]]) -> str:
    """
    Build the prompt with numbered source blocks visible to the model.
    Each source shows its citation metadata so the model can describe
    where it came from when asked.
    """
    source_lines: List[str] = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata", {})
        file_name = meta.get("file_name", "unknown")
        location = meta.get("page_or_timestamp", "")
        sender = meta.get("sender", "")
        source_type = meta.get("source_type", "")
        text = chunk.get("text", "").strip()

        header_parts = [f"[{i}] {file_name}"]
        if location:
            header_parts.append(location)
        if sender:
            header_parts.append(f"({sender})")
        if source_type:
            header_parts.append(f"[{source_type}]")

        source_lines.append(" | ".join(header_parts))
        source_lines.append(text)
        source_lines.append("")

    sources_block = "\n".join(source_lines)

    return (
        f"SOURCES:\n"
        f"{'=' * 60}\n"
        f"{sources_block}"
        f"{'=' * 60}\n\n"
        f"QUESTION: {query}\n\n"
        f"ANSWER (cite every claim with [N]):"
    )


# ── Citation extraction ───────────────────────────────────────

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _extract_citations(
    answer: str,
    chunks: List[Dict[str, Any]],
) -> List[Citation]:
    """
    Find all [N] markers used in the answer and build structured Citation objects.
    Only includes indices that actually appear in the answer text.
    """
    if "not found" in answer.lower():
        return []

    used_indices = sorted(set(int(m) for m in _CITATION_RE.findall(answer)))
    citations: List[Citation] = []

    for idx in used_indices:
        chunk_pos = idx - 1   # [1] → index 0
        if chunk_pos < 0 or chunk_pos >= len(chunks):
            continue
        chunk = chunks[chunk_pos]
        meta = chunk.get("metadata", {})
        text = chunk.get("text", "")
        snippet = text[:250].strip()
        if len(text) > 250:
            snippet += "…"

        citations.append(Citation(
            index=idx,
            file_name=meta.get("file_name", "unknown"),
            location=meta.get("page_or_timestamp", ""),
            snippet=snippet,
            source_type=meta.get("source_type", ""),
            sender=meta.get("sender") or None,
        ))

    return citations


# ── Ollama client ─────────────────────────────────────────────

class Generator:
    """
    Citation-grounded LLM generator using Ollama.

    Usage:
        gen = Generator(config)
        # Streaming (yields tokens):
        async for token in gen.generate_stream(query, chunks, model="llama3.2"):
            print(token, end="", flush=True)

        # Non-streaming (returns CitationResult):
        result = await gen.generate(query, chunks)
        print(result.answer)
        for c in result.citations:
            print(f"[{c.index}] {c.file_name} — {c.location}")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        ollama_cfg = cfg.get("ollama", {})
        self.ollama_host = ollama_cfg.get("host", "http://localhost:11434")
        self.default_text_model = ollama_cfg.get("default_text_model", "llama3.2")
        self.default_vision_model = ollama_cfg.get("default_vision_model", "llava")
        self.timeout = float(ollama_cfg.get("request_timeout", 120))

    def _resolve_model(self, model: Optional[str]) -> str:
        return model or self.default_text_model

    # ── Async streaming ───────────────────────────────────────

    async def generate_stream(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Yield tokens as they stream from Ollama.
        The final yield (after all tokens) is a special JSON sentinel:
            __CITATIONS__:{...}
        which the API layer strips and sends separately.
        """
        resolved_model = self._resolve_model(model)
        prompt = _build_prompt(query, chunks)
        full_answer: List[str] = []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.ollama_host}/api/generate",
                    json={
                        "model": resolved_model,
                        "system": _SYSTEM_PROMPT,
                        "prompt": prompt,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = data.get("response", "")
                        if token:
                            full_answer.append(token)
                            yield token
                        if data.get("done", False):
                            break
        except httpx.TimeoutException:
            logger.error(f"Ollama request timed out after {self.timeout}s")
            yield "\n[Error: The AI model took too long to respond. It may be loading into memory. Please try your request again in a moment.]"
            return
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            yield f"\n[Error connecting to Ollama: {e}]"
            return

        # Post-process citations and yield as sentinel
        answer_text = "".join(full_answer)
        citations = _extract_citations(answer_text, chunks)
        citations_payload = {
            "citations": [
                {
                    "index": c.index,
                    "file_name": c.file_name,
                    "location": c.location,
                    "snippet": c.snippet,
                    "source_type": c.source_type,
                    "sender": c.sender,
                }
                for c in citations
            ]
        }
        yield f"\n__CITATIONS__:{json.dumps(citations_payload)}"

    # ── Non-streaming (for tests + structured DB answers) ─────

    async def generate(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: Optional[str] = None,
        used_sql: bool = False,
    ) -> CitationResult:
        """Full non-streaming generate. Collects all tokens then extracts citations."""
        resolved_model = self._resolve_model(model)
        prompt = _build_prompt(query, chunks)
        full_answer = ""

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.ollama_host}/api/generate",
                    json={
                        "model": resolved_model,
                        "system": _SYSTEM_PROMPT,
                        "prompt": prompt,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                full_answer = data.get("response", "")
        except httpx.TimeoutException:
            logger.error(f"Ollama request timed out after {self.timeout}s")
            full_answer = "[Error: The AI model took too long to respond. It may be loading into memory. Please try your request again in a moment.]"
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            full_answer = f"[Error connecting to Ollama: {e}]"

        citations = _extract_citations(full_answer, chunks)
        return CitationResult(
            answer=full_answer,
            citations=citations,
            model=resolved_model,
            used_sql=used_sql,
        )

    # ── Contextualize Query ───────────────────────────────────

    async def contextualize_query(self, query: str, history: List[Dict[str, str]], model: Optional[str] = None) -> str:
        """Rewrite the query to be standalone given the chat history."""
        if not history:
            return query
            
        resolved_model = self._resolve_model(model)
        
        # Build conversation history string
        hist_lines = []
        for msg in history:
            role = "User" if msg.get("role") == "user" else "Assistant"
            hist_lines.append(f"{role}: {msg.get('content')}")
        hist_str = "\n".join(hist_lines)
        
        prompt = (
            "Given a chat history and the latest user question "
            "which might reference context in the chat history, formulate a standalone question "
            "which can be understood without the chat history. Do NOT answer the question, "
            "just reformulate it if needed and otherwise return it as is.\n\n"
            f"Chat History:\n{hist_str}\n\n"
            f"Latest Question: {query}\n\n"
            "Standalone Question:"
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": resolved_model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            rewritten = data.get("response", "").strip()
            return rewritten if rewritten else query

    # ── SQL answer wrapper ────────────────────────────────────

    async def generate_from_sql(
        self,
        query: str,
        sql_results: List[Dict[str, Any]],
        source_file: str = "structured_db",
        model: Optional[str] = None,
    ) -> CitationResult:
        """
        Generate a natural-language answer from SQL query results.
        Wraps the SQL results as fake chunks so the same citation machinery works.
        """
        # Convert SQL rows to pseudo-chunks
        rows_text = json.dumps(sql_results, indent=2, default=str)
        pseudo_chunks = [{
            "chunk_id": "sql_result",
            "text": f"Database query results:\n{rows_text}",
            "metadata": {
                "file_name": source_file,
                "page_or_timestamp": "SQL query result",
                "source_type": "structured_db",
                "sender": None,
            },
            "score": 1.0,
        }]
        result = await self.generate(query, pseudo_chunks, model=model, used_sql=True)
        return result

    # ── Sync streaming (for simple callers) ──────────────────

    def generate_stream_sync(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> Iterator[str]:
        """
        Synchronous streaming wrapper using httpx sync client.
        Yields tokens, then a final __CITATIONS__:{...} sentinel.
        """
        resolved_model = self._resolve_model(model)
        prompt = _build_prompt(query, chunks)
        full_answer: List[str] = []

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{self.ollama_host}/api/generate",
                json={
                    "model": resolved_model,
                    "system": _SYSTEM_PROMPT,
                    "prompt": prompt,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = data.get("response", "")
                    if token:
                        full_answer.append(token)
                        yield token
                    if data.get("done", False):
                        break

        answer_text = "".join(full_answer)
        citations = _extract_citations(answer_text, chunks)
        citations_payload = {
            "citations": [
                {
                    "index": c.index,
                    "file_name": c.file_name,
                    "location": c.location,
                    "snippet": c.snippet,
                    "source_type": c.source_type,
                    "sender": c.sender,
                }
                for c in citations
            ]
        }
        yield f"\n__CITATIONS__:{json.dumps(citations_payload)}"


# ── Prompt inspection utility ─────────────────────────────────

def build_prompt_preview(query: str, chunks: List[Dict[str, Any]]) -> str:
    """Return the prompt that would be sent to the model — useful for debugging."""
    return _SYSTEM_PROMPT + "\n\n" + _build_prompt(query, chunks)
