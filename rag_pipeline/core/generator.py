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
    full_text: str        # complete text of the source chunk
    source_type: str = ""
    sender: Optional[str] = None


@dataclass
class CitationResult:
    answer: str                          # full answer with [N] markers
    citations: List[Citation] = field(default_factory=list)
    model: str = ""
    used_sql: bool = False               # True if answer came from structured DB
    structure: Dict[str, bool] = field(default_factory=dict)  # has_headers, has_bullets


# ── Prompt builder ────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert research assistant. You answer questions using ONLY the
numbered sources provided. You are FORBIDDEN from using any external knowledge.

Formatting rules — follow them exactly:
1. Start with a direct 1-2 sentence answer to the question.
2. Follow with supporting detail in short paragraphs OR bullet points
   (use bullets when listing multiple items, steps, or comparisons;
   use short paragraphs for narrative or explanatory content).
3. Use a markdown header (## or ###) to break up sections ONLY if the
   answer covers more than one distinct sub-topic. Do NOT add headers
   for a short, single-topic answer.
4. Bold key terms, names, figures, or dates the user would want to scan
   quickly, e.g. **Indian Ocean**, **$4,200**, **March 14**.
5. Cite every factual claim with its source number immediately after the
   claim, e.g. [1] or [1][2]. Do NOT bunch citations at the end of a
   paragraph.
6. If sources disagree, say so explicitly.
7. If the answer is not in the sources, output EXACTLY and ONLY:
   Not found in documents.
   Do not add any other words.
8. Do NOT add a "Sources" section — that is generated separately.
9. Do NOT restate these instructions in your answer.
"""


def _build_prompt(query: str, chunks: List[Dict[str, Any]]) -> str:
    """
    Build the Perplexity-style prompt with numbered source blocks.
    Each block shows file name, location, and source type so the model
    can produce precise inline citations.
    """
    source_parts: List[str] = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata", {})
        file_name = meta.get("file_name", "unknown")
        location = meta.get("page_or_timestamp", "") or "—"
        sender = meta.get("sender", "")
        source_type = meta.get("source_type", "")
        text = chunk.get("text", "").strip()

        meta_parts = [f"File: {file_name}", f"Location: {location}"]
        if source_type:
            meta_parts.append(f"Type: {source_type}")
        if sender:
            meta_parts.append(f"Sender: {sender}")

        source_parts.append(
            f"[{i}] {' | '.join(meta_parts)}\n{text}"
        )

    numbered_context = "\n\n".join(source_parts)

    return (
        f"Sources:\n"
        f"{'-' * 60}\n"
        f"{numbered_context}\n"
        f"{'-' * 60}\n\n"
        f"Question: {query}\n\n"
        f"Answer:"
    )


# ── Structure analysis ────────────────────────────────────────

def analyze_structure(answer_text: str) -> Dict[str, bool]:
    """
    Detect whether the model's response contains markdown headers or
    bullet lists. Used by the frontend to decide whether to render a
    jump-list nav above the answer.
    """
    has_headers = bool(re.search(r"^#{2,3}\s", answer_text, re.MULTILINE))
    has_bullets = bool(re.search(r"^[-*]\s", answer_text, re.MULTILINE))
    return {"has_headers": has_headers, "has_bullets": has_bullets}


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
        
        source_type = meta.get("source_type", "")
        sender = meta.get("sender") or None
        if source_type in ("pdf", "docx", "pptx", "excel", "csv"):
            sender = None

        citations.append(Citation(
            index=idx,
            file_name=meta.get("file_name", "unknown"),
            location=meta.get("location_label", ""),
            full_text=text.strip(),
            source_type=source_type,
            sender=sender,
        ))

    # Fallback: if the model didn't include any [N] markers (common with small models),
    # auto-cite all retrieved chunks so the Sources panel is never empty.
    if not citations and "not found" not in answer.lower():
        logger.debug("No [N] markers found in answer — falling back to auto-citing all retrieved chunks")
        for i, chunk in enumerate(chunks, start=1):
            meta = chunk.get("metadata", {})
            text = chunk.get("text", "")
            source_type = meta.get("source_type", "")
            sender = meta.get("sender") or None
            if source_type in ("pdf", "docx", "pptx", "excel", "csv"):
                sender = None
            citations.append(Citation(
                index=i,
                file_name=meta.get("file_name", "unknown"),
                location=meta.get("location_label", ""),
                full_text=text.strip(),
                source_type=source_type,
                sender=sender,
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
        self.grader_model = ollama_cfg.get("grader_model", self.default_text_model)
        self.default_vision_model = ollama_cfg.get("default_vision_model", "llava")
        self.timeout = float(ollama_cfg.get("request_timeout", 120))

    def _resolve_model(self, model: Optional[str]) -> str:
        return model or self.default_text_model

    async def grade_context(self, query: str, chunks: List[Dict[str, Any]]) -> bool:
        """
        Algorithmic Grader: Evaluate if the retrieved context contains the answer to the query.
        """
        if not chunks:
            return False
        
        context_parts = []
        for c in chunks:
            meta = c.get("metadata", {})
            file_name = meta.get("file_name", "unknown")
            source_type = meta.get("source_type", "unknown")
            text = c.get("text", "")
            context_parts.append(f"[File: {file_name} | Type: {source_type}]\n{text}")
        context = "\n\n".join(context_parts)
        prompt = (
            "Evaluate if the following context contains the answer to the user's query. "
            "Output EXACTLY 'True' if it does, and 'False' if it does not. Do not output anything else.\n\n"
            f"Query: {query}\n\nContext: {context}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.ollama_host}/api/generate",
                    json={
                        "model": self.grader_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.0}
                    }
                )
                if resp.status_code == 200:
                    result = resp.json().get("response", "").strip()
                    if "False" in result or "false" in result:
                        return False
                return True
        except Exception as e:
            logger.warning(f"Algorithmic Grader failed: {e}. Defaulting to True.")
            return True

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

        # Post-process citations + structure and yield as sentinel
        answer_text = "".join(full_answer)
        citations = _extract_citations(answer_text, chunks)
        structure = analyze_structure(answer_text)
        citations_payload = {
            "citations": [
                {
                    "index": c.index,
                    "file_name": c.file_name,
                    "location": c.location,
                    "full_text": c.full_text,
                    "source_type": c.source_type,
                    "sender": c.sender,
                }
                for c in citations
            ],
            "structure": structure,
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
        structure = analyze_structure(full_answer)
        return CitationResult(
            answer=full_answer,
            citations=citations,
            model=resolved_model,
            used_sql=used_sql,
            structure=structure,
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
        structure = analyze_structure(answer_text)
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
            ],
            "structure": structure,
        }
        yield f"\n__CITATIONS__:{json.dumps(citations_payload)}"


# ── Prompt inspection utility ─────────────────────────────────

def build_prompt_preview(query: str, chunks: List[Dict[str, Any]]) -> str:
    """Return the prompt that would be sent to the model — useful for debugging."""
    return _SYSTEM_PROMPT + "\n\n" + _build_prompt(query, chunks)

# ── KG Extractor ──────────────────────────────────────────────

async def extract_kg_relationships(text: str, model: str = "llama3.2", host: str = "http://localhost:11434") -> List[Dict[str, Any]]:
    """
    Extracts knowledge graph entities and relationships from the provided text.
    Returns a list of dicts: {"source": "node1", "target": "node2", "relation": "rel"}
    """
    prompt = (
        "Extract key entities (such as People, Organizations, Projects, Concepts, and Invoices) "
        "and the relationships between them from the following text.\n"
        "Output a strictly valid JSON array of objects, where each object has exactly three string fields: "
        "'source', 'target', and 'relation'. Do not output any markdown formatting, explanations, or other text.\n\n"
        f"Text:\n{text[:3000]}"
    )
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0},
                    "format": "json"
                }
            )
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                try:
                    data = json.loads(result)
                    if isinstance(data, list):
                        return [d for d in data if isinstance(d, dict) and "source" in d and "target" in d and "relation" in d]
                    elif isinstance(data, dict):
                        if "edges" in data and isinstance(data["edges"], list):
                            return data["edges"]
                        elif "source" in data and "target" in data and "relation" in data:
                            return [data]
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.warning(f"Failed to extract KG relationships: {e}")
        
    return []
