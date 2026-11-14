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
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from rag_pipeline.structured_db.numeric import as_number as _as_number, format_summary, is_identifier

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
    file_key: Optional[str] = None


@dataclass
class CitationResult:
    answer: str                          # full answer with [N] markers
    citations: List[Citation] = field(default_factory=list)
    model: str = ""
    used_sql: bool = False               # True if answer came from structured DB
    structure: Dict[str, bool] = field(default_factory=dict)  # has_headers, has_bullets


# ── Prompt builder ────────────────────────────────────────────

# Raw string: the LaTeX examples below must reach the model as literal
# backslashes, not as \r, \t and \f control characters.
_SYSTEM_PROMPT = r"""You are an expert research assistant. You answer questions using ONLY the
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
   quickly (names, amounts, dates). Use only values that appear in the sources.
5. Cite every factual claim with its source number immediately after the
   claim, e.g. [1] or [1][2]. Do NOT bunch citations at the end of a
   paragraph.
6. If sources disagree, say so explicitly.
7. If the answer is not in the sources, output EXACTLY and ONLY:
   Not found in documents.
   Do not add any other words.
8. Do NOT add a "Sources" section — that is generated separately.
9. Do NOT restate these instructions in your answer.

When writing chemical formulas, equations, or any scientific notation, use plain
Unicode characters only:
- Subscripts: ₀ ₁ ₂ ₃ ₄ ₅ ₆ ₇ ₈ ₉
- Superscripts for ions/charges: ⁺ ⁻ ²⁺ ³⁺ etc.
- Reaction arrows: →
- Fractions as simple text: 1/2, not \frac{1}{2}

Never use LaTeX syntax under any circumstance — no dollar signs, no \text{},
\frac{}, \rightarrow, or any backslash commands.

Example of CORRECT formatting:
2Fe(OH)₂ + ½O₂ + H₂O → Fe₂O₃·3H₂O

Example of INCORRECT formatting — do not produce output like this:
$2\text{Fe(OH)}_2 + \frac{1}{2}\text{O}_2 + \text{H}_2\text{O} \rightarrow \text{Fe}_2\text{O}_3 \cdot 3\text{H}_2\text{O}$
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
        location = meta.get("location_label", "") or "—"
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


# ── Formatting Guarantee ──────────────────────────────────────
# Applied once to the finished answer, never per streamed token (a LaTeX
# construct can span several tokens). Deliberately conservative: it must not
# rewrite ordinary identifiers such as invoice_2024 or file_3.pdf.

_LATEX_CLEANUP_RULES = [
    (r"\$\$([^$]+)\$\$", r"\1"),                                        # strip block $$ delimiters
    (r"\$(?=[^$\s])([^$]*?[\\_^][^$]*?)\$", r"\1"),                    # strip inline $..$ only around math
    (r"\\text\{([^}]*)\}", r"\1"),                                    # \text{Fe} -> Fe
    (r"\\frac\{(\d+)\}\{(\d+)\}", lambda m: f"{m.group(1)}/{m.group(2)}"),  # \frac{1}{2} -> 1/2
    (r"\\rightarrow", "→"),
    (r"\\to\b", "→"),
    (r"\\cdot", "·"),
    (r"\\left|\\right", ""),
    (r"\\,", " "),
]

_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUPERSCRIPT_MAP = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")

# Subscripts only after an element symbol or a closing paren: H_2O, Fe(OH)_2.
_SUBSCRIPT_RE = re.compile(r"(\b[A-Z][a-z]?|\))_(?:\{\s*(\d+)\s*\}|(\d+))")
_SUPERSCRIPT_BRACED_RE = re.compile(r"\^\{\s*([\d+-]+)\s*\}")
_SUPERSCRIPT_RE = re.compile(r"(?<=[A-Za-z0-9\)])\^(\d+[+-]?|[+-])(?!\w)")


def clean_formatting_artifacts(text: str) -> str:
    for pattern, repl in _LATEX_CLEANUP_RULES:
        text = re.sub(pattern, repl, text)
    text = _SUBSCRIPT_RE.sub(lambda m: m.group(1) + (m.group(2) or m.group(3)).translate(_SUBSCRIPT_MAP), text)
    text = _SUPERSCRIPT_BRACED_RE.sub(lambda m: m.group(1).translate(_SUPERSCRIPT_MAP), text)
    text = _SUPERSCRIPT_RE.sub(lambda m: m.group(1).translate(_SUPERSCRIPT_MAP), text)
    return text


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

NOT_FOUND_ANSWER = "Not found in documents."
_CITATION_RE = re.compile(r"\[(\d+)\]")


def is_not_found(answer: str) -> bool:
    """True only when the model gave the 'nothing in the sources' answer —
    not whenever the phrase 'not found' appears inside a real answer."""
    normalized = answer.strip().strip("*_ ").rstrip(".").lower()
    return normalized.startswith(("not found in documents", "not found in the documents"))


def _citation_for(index: int, chunk: Dict[str, Any]) -> Citation:
    meta = chunk.get("metadata", {})
    source_type = meta.get("source_type", "")
    sender = meta.get("sender") or None
    if source_type in ("pdf", "docx", "pptx", "excel", "csv", "text"):
        sender = None
    return Citation(
        index=index,
        file_name=meta.get("file_name", "unknown"),
        location=meta.get("location_label", ""),
        full_text=chunk.get("text", "").strip(),
        source_type=source_type,
        sender=sender,
        file_key=meta.get("file_key") if isinstance(meta.get("file_key"), str) else None,
    )


def _extract_citations(
    answer: str,
    chunks: List[Dict[str, Any]],
) -> List[Citation]:
    """
    Find all [N] markers used in the answer and build structured Citation objects.
    Only includes indices that actually appear in the answer text.
    """
    if is_not_found(answer):
        return []

    used_indices = sorted(set(int(m) for m in _CITATION_RE.findall(answer)))
    citations = [
        _citation_for(idx, chunks[idx - 1])
        for idx in used_indices
        if 1 <= idx <= len(chunks)
    ]

    # Fallback: if the model didn't include any [N] markers (common with small models),
    # auto-cite all retrieved chunks so the Sources panel is never empty.
    if not citations:
        logger.debug("No [N] markers found in answer — auto-citing all retrieved chunks")
        citations = [_citation_for(i, c) for i, c in enumerate(chunks, start=1)]
    return citations


_INVOICE_FIELDS = ("vendor", "invoice_number", "invoice_date", "due_date", "total_amount", "currency", "file_name")


def flatten_sql_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ledger rows → their spreadsheet columns; invoice rows → the useful fields."""
    if isinstance(row.get("row_data"), dict):
        return {**row["row_data"], "file": row.get("file_name"), "sheet": row.get("sheet_name") or None}
    return {k: row.get(k) for k in _INVOICE_FIELDS if row.get(k) is not None}


def summarize_numeric_columns(rows: List[Dict[str, Any]]) -> List[str]:
    """
    Exact totals computed in code. Small local models add up numbers badly,
    so the prompt hands them the arithmetic instead of asking for it.
    Only columns where every non-empty value is numeric are summarized.
    """
    lines: List[str] = []
    columns = {k for r in rows for k in r}
    for col in sorted(columns):
        values = [r.get(col) for r in rows if r.get(col) not in (None, "")]
        numbers = [_as_number(v) for v in values]
        if not values or any(n is None for n in numbers):
            continue
        if is_identifier(col):
            continue
        nums = [n for n in numbers if n is not None]
        lines.append(format_summary(col, sum(nums), len(nums), sum(nums) / len(nums), min(nums), max(nums)))
    return lines


def sql_rows_to_chunks(
    sql_results: List[Dict[str, Any]], label: str = "structured_db", *,
    total_count: Optional[int] = None, numeric_summary: Optional[List[str]] = None,
    file_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Wrap SQL rows as a pseudo-chunk so the same prompt/citation path works."""
    rows = [flatten_sql_row(r) for r in sql_results]
    summary = numeric_summary if numeric_summary is not None else summarize_numeric_columns(rows)
    files = file_names if file_names is not None else sorted({str(r.get("file") or r.get("file_name")) for r in rows if r.get("file") or r.get("file_name")})
    count = total_count if total_count is not None else len(rows)
    preview = rows[:50]
    parts = [f"Matching rows from {', '.join(files) or 'the database'} ({count} row(s))."]
    if summary:
        parts.append("Exact totals, already calculated — use these numbers rather than adding rows yourself:\n" + "\n".join(summary))
    if len(preview) < count:
        parts.append(
            f"Only the first {len(preview)} of {count} matching rows are shown. "
            f"The exact totals above cover all {count} matching rows, not just this preview. "
            "Tell the user this row list is truncated when listing individual records."
        )
    parts.append("Rows:\n" + json.dumps(preview, indent=1, default=str, ensure_ascii=False))
    return [{
        "chunk_id": "sql_result",
        "text": "\n\n".join(parts),
        "metadata": {
            "file_name": ", ".join(files) or label,
            "location_label": f"{count} matching row(s)" + (f"; showing {len(preview)}" if len(preview) < count else ""),
            "source_type": "structured_db",
            "sender": None,
        },
        "score": 1.0,
    }]


# ── Ollama client ─────────────────────────────────────────────

class GenerationError(Exception):
    """A user-presentable failure talking to Ollama."""


_FOLLOW_UP_RE = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|he|she|him|her|his|"
    r"the same|above|previous|earlier|also|more|else)\b|^(and|but|what about|how about|why)\b",
    re.IGNORECASE,
)


def looks_like_follow_up(query: str) -> bool:
    """Cheap check so standalone questions skip the rewrite LLM call."""
    return len(query.split()) <= 4 or bool(_FOLLOW_UP_RE.search(query))


class Generator:
    """
    Citation-grounded LLM generator using Ollama.

    Usage:
        gen = Generator(config)
        tokens = []
        async for token in gen.generate_stream(query, chunks):
            tokens.append(token)
        result = gen.finalize("".join(tokens), chunks, model)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._config = cfg
        ollama_cfg = cfg.get("ollama", {})
        self.ollama_host = ollama_cfg.get("host", "http://localhost:11434")
        self._grader_model = ollama_cfg.get("grader_model")
        self.default_vision_model = ollama_cfg.get("default_vision_model", "llava")
        self.timeout = float(ollama_cfg.get("request_timeout", 300))
        # Keep the model loaded between questions (Ollama's default is 5m).
        self.keep_alive = ollama_cfg.get("keep_alive", "30m")
        # Ollama silently truncates prompts beyond num_ctx; set it explicitly.
        self.num_ctx = int(ollama_cfg.get("num_ctx", 8192))
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def default_text_model(self) -> str:
        """The user's chosen model (settings.json), else the config default."""
        from rag_pipeline.settings import get_chat_model
        return get_chat_model(self._config)

    @property
    def grader_model(self) -> str:
        return self._grader_model or self.default_text_model

    def _resolve_model(self, model: Optional[str]) -> str:
        return model or self.default_text_model

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        return self._http

    def _payload(self, model: str, prompt: str, *, system: Optional[str] = None,
                 stream: bool = False, temperature: Optional[float] = None) -> Dict[str, Any]:
        options: Dict[str, Any] = {"num_ctx": self.num_ctx}
        if temperature is not None:
            options["temperature"] = temperature
        payload: Dict[str, Any] = {
            "model": model, "prompt": prompt, "stream": stream,
            "keep_alive": self.keep_alive, "options": options,
        }
        if system:
            payload["system"] = system
        return payload

    async def _complete(self, model: str, prompt: str, **kwargs: Any) -> str:
        """Single non-streaming completion. Raises GenerationError."""
        try:
            resp = await self._client().post(
                f"{self.ollama_host}/api/generate", json=self._payload(model, prompt, **kwargs)
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except httpx.HTTPStatusError as e:
            raise GenerationError(_describe_status_error(e, model)) from e
        except httpx.HTTPError as e:
            raise GenerationError(_describe_connection_error(e, model)) from e

    async def grade_context(self, query: str, chunks: List[Dict[str, Any]]) -> bool:
        """
        Optional LLM relevance check (config retrieval.use_llm_grader). Costs a
        full extra model call, so it's off by default; the answer prompt
        already tells the model to reply "Not found in documents." when the
        sources don't contain the answer.
        """
        if not chunks:
            return False
        context = "\n\n".join(
            f"[File: {c.get('metadata', {}).get('file_name', 'unknown')}]\n{c.get('text', '')}" for c in chunks
        )
        prompt = (
            "Evaluate if the following context contains the answer to the user's query. "
            "Output EXACTLY 'True' if it does, and 'False' if it does not. Do not output anything else.\n\n"
            f"Query: {query}\n\nContext: {context}"
        )
        try:
            result = await self._complete(self.grader_model, prompt, temperature=0.0)
        except GenerationError as e:
            logger.warning("Grader failed: %s. Defaulting to relevant.", e)
            return True
        return "false" not in result.strip().lower()

    # ── Streaming ─────────────────────────────────────────────

    async def generate_stream(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Yield raw answer tokens from Ollama. Raises GenerationError."""
        resolved_model = self._resolve_model(model)
        payload = self._payload(resolved_model, _build_prompt(query, chunks), system=_SYSTEM_PROMPT, stream=True)
        try:
            async with self._client().stream("POST", f"{self.ollama_host}/api/generate", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise GenerationError(str(data["error"]))
                    token = data.get("response", "")
                    if token:
                        yield token
                    if data.get("done", False):
                        break
        except httpx.HTTPStatusError as e:
            raise GenerationError(_describe_status_error(e, resolved_model)) from e
        except httpx.HTTPError as e:
            raise GenerationError(_describe_connection_error(e, resolved_model)) from e

    def finalize(self, raw_answer: str, chunks: List[Dict[str, Any]], model: str,
                 used_sql: bool = False) -> CitationResult:
        """Clean the finished answer and attach the citations it actually uses."""
        answer = clean_formatting_artifacts(raw_answer).strip()
        if is_not_found(answer):
            answer = NOT_FOUND_ANSWER
        return CitationResult(
            answer=answer,
            citations=_extract_citations(answer, chunks),
            model=model,
            used_sql=used_sql,
            structure=analyze_structure(answer),
        )

    # ── Non-streaming ─────────────────────────────────────────

    async def generate(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: Optional[str] = None,
        used_sql: bool = False,
    ) -> CitationResult:
        """Full non-streaming generate. Raises GenerationError."""
        resolved_model = self._resolve_model(model)
        raw = await self._complete(resolved_model, _build_prompt(query, chunks), system=_SYSTEM_PROMPT)
        return self.finalize(raw, chunks, resolved_model, used_sql=used_sql)

    async def generate_from_sql(
        self,
        query: str,
        sql_results: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> CitationResult:
        return await self.generate(query, sql_rows_to_chunks(sql_results), model=model, used_sql=True)

    # ── Contextualize Query ───────────────────────────────────

    async def contextualize_query(self, query: str, history: List[Dict[str, str]], model: Optional[str] = None) -> str:
        """Rewrite a follow-up question to be standalone. Skips the LLM call
        for questions that don't look like follow-ups."""
        if not history or not looks_like_follow_up(query):
            return query

        hist_str = "\n".join(
            f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('content') or '')[:1000]}"
            for m in history[-6:]
        )
        prompt = (
            "Given a chat history and the latest user question "
            "which might reference context in the chat history, formulate a standalone question "
            "which can be understood without the chat history. Do NOT answer the question, "
            "just reformulate it if needed and otherwise return it as is. "
            "Output only the question.\n\n"
            f"Chat History:\n{hist_str}\n\n"
            f"Latest Question: {query}\n\n"
            "Standalone Question:"
        )
        try:
            rewritten = (await self._complete(self._resolve_model(model), prompt, temperature=0.0)).strip()
        except GenerationError as e:
            logger.warning("Query rewrite failed (%s); using original question.", e)
            return query
        return rewritten.strip('"').strip() or query

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()


def _describe_status_error(e: httpx.HTTPStatusError, model: str) -> str:
    code = e.response.status_code
    try:
        detail = e.response.json().get("error", "")
    except ValueError:
        detail = ""
    if code == 404:
        return f"The model '{model}' isn't installed in Ollama. Download it on the Models page or pick another model."
    if "cloud" in model.lower() and code == 401:
        return "This cloud model needs authorization. Run `ollama signin` in a terminal, or use a local model."
    return f"Ollama returned HTTP {code}{': ' + detail if detail else ''}."


def _describe_connection_error(e: Exception, model: str) -> str:
    if isinstance(e, (httpx.ReadTimeout, httpx.WriteTimeout)):
        return f"The model '{model}' took too long to respond. Try a smaller model."
    return "Can't reach Ollama. Make sure it's installed and running."


# ── Prompt inspection utility ─────────────────────────────────

def build_prompt_preview(query: str, chunks: List[Dict[str, Any]]) -> str:
    """Return the prompt that would be sent to the model — useful for debugging."""
    return _SYSTEM_PROMPT + "\n\n" + _build_prompt(query, chunks)

# ── KG Extractor ──────────────────────────────────────────────

def _sample_text(text: str, source_type: str, max_tokens: int = 2000) -> str:
    # Approximate 1 token = 4 chars
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    
    if source_type in ["txt", "md", "docx", "pdf", "doc"]:
        head_chars = int(max_chars * 0.7)
        tail_chars = max_chars - head_chars
        head = text[:head_chars]
        tail = text[-tail_chars:]
        return head + "\n\n...[OMITTED]...\n\n" + tail
    else:
        chunk_size = max_chars // 3
        start_chunk = text[:chunk_size]
        mid_idx = len(text) // 2 - chunk_size // 2
        mid_chunk = text[mid_idx:mid_idx + chunk_size]
        end_idx = len(text) - chunk_size
        end_chunk = text[end_idx:]
        return (
            start_chunk + "\n\n...[OMITTED]...\n\n" +
            mid_chunk + "\n\n...[OMITTED]...\n\n" +
            end_chunk
        )

KG_MAX_OUTPUT_TOKENS = 1024


def _salvage_truncated_edges(raw: str) -> Optional[List[Any]]:
    """
    Parse a JSON array (or {"edges": [...]}) that was cut off by the output
    cap, keeping every object completed before the cut. None if nothing usable.
    """
    start = raw.find("[")
    if start < 0:
        return None
    end = len(raw)
    for _ in range(200):
        end = raw.rfind("}", start, end)
        if end < 0:
            return None
        try:
            data = json.loads(raw[start:end + 1] + "]")
            if isinstance(data, list) and data:
                return data
        except ValueError:
            pass
    return None


async def extract_kg_relationships(
    text: str, model: str = "llama3.2", host: str = "http://localhost:11434",
    source_type: str = "doc", timeout: float = 300.0,
    keep_alive: Optional[str] = None, num_ctx: Optional[int] = None,
    max_output_tokens: int = KG_MAX_OUTPUT_TOKENS,
) -> List[Dict[str, Any]]:
    """
    Extracts knowledge graph entities and relationships from the provided text.
    Returns a list of dicts: {"source": "node1", "target": "node2", "relation": "rel"}
    Raises GenerationError on transport, model, or invalid-output failures.
    An empty list means extraction succeeded with no relationships.
    """
    sampled_text = _sample_text(text, source_type, max_tokens=2000)
    prompt = (
        "Extract key entities (such as People, Organizations, Projects, Concepts, and Invoices) "
        "and the relationships between them from the following text.\n"
        "Output a strictly valid JSON array of objects, where each object has exactly five string fields: "
        "'source', 'source_type', 'target', 'target_type', and 'relation'.\n"
        "For the type fields, you MUST choose one of the following exact categories: "
        "'Concept', 'Material', 'Process', 'Organization', 'Person', 'Location', 'Technology', or 'Other'.\n"
        "Do not output any markdown formatting, explanations, or other text.\n\n"
        f"Text:\n{sampled_text}"
    )
    
    # Cap the answer: in JSON mode small models can repeat edges until the
    # context is full, which on a CPU means minutes, then a timeout.
    options: Dict[str, Any] = {"temperature": 0.0, "num_predict": max_output_tokens}
    if num_ctx:
        options["num_ctx"] = num_ctx   # Ollama silently truncates beyond its (smaller) default
    body: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "options": options, "format": "json"}
    if keep_alive:
        body["keep_alive"] = keep_alive   # keep the model loaded between the build's calls
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.post(f"{host}/api/generate", json=body)
            resp.raise_for_status()
            payload = resp.json()
            raw = payload["response"]
            try:
                data = json.loads(raw)
            except ValueError:
                # Hit the output cap mid-answer: keep the edges that were complete.
                data = _salvage_truncated_edges(raw) if payload.get("done_reason") == "length" else None
                if data is None:
                    raise
    except httpx.HTTPStatusError as error:
        raise GenerationError(_describe_status_error(error, model)) from error
    except httpx.RequestError as error:
        raise GenerationError(_describe_connection_error(error, model)) from error
    except (ValueError, KeyError, TypeError) as error:
        raise GenerationError("Ollama returned invalid relationship data. Build the graph again to retry.") from error

    if isinstance(data, dict):
        if "edges" in data:
            data = data["edges"]
        elif all(key in data for key in ("source", "target", "relation")):
            data = [data]
    if not isinstance(data, list):
        logger.warning("KG extraction: unexpected answer shape: %.300s", raw)
        raise GenerationError("Ollama returned invalid relationship data. Build the graph again to retry.")
    edges = [edge for edge in data if _is_valid_edge(edge)]
    if data and not edges:
        logger.warning("KG extraction: no usable edges in answer: %.300s", raw)
        raise GenerationError("Ollama returned invalid relationship data. Build the graph again to retry.")
    if len(edges) < len(data):
        # Small models often get one edge wrong; keep the good ones rather than
        # rejecting the whole file (which would then fail again on every retry).
        logger.info("KG extraction: dropped %d malformed edge(s) of %d", len(data) - len(edges), len(data))
    return edges


def _is_valid_edge(edge: Any) -> bool:
    return isinstance(edge, dict) and all(
        isinstance(edge.get(key), str) and edge[key].strip() for key in ("source", "target", "relation")
    ) and all(not (key in edge and not isinstance(edge[key], str)) for key in ("source_type", "target_type"))
