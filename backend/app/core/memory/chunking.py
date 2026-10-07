"""Structure-aware recursive chunking with overlap (RAG upgrade).

Replaces the old whitespace-split ``~2000 chars, no overlap`` fallback in
``LocalPageIndexService._build_simple_tree`` which silently dropped context
at chunk boundaries (tables, sentences cut in half) and produced flat
``Section N`` titles with ``[:200]`` summaries.

Features:
- Split hierarchy: paragraphs → sentences → words (never splits inside a
  sentence unless it exceeds ``max_chars``).
- Configurable overlap (default 200 chars on a 1500-char chunk) so entities
  and figures near boundaries stay retrievable from either side.
- Heading detection (markdown ``#`` / ALL-CAPS / numbered) → meaningful
  node titles + section path preserved in metadata for citations.
- Pure stdlib, deterministic, no extra dependencies.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_MAX_CHARS = 1500
DEFAULT_OVERLAP_CHARS = 200

_HEADING_RE = re.compile(r"^(#{1,4}\s+|\d+(?:\.\d+)*\.?\s+[A-Z].*|[A-Z][A-Z0-9 ,&'’\-/]{8,80})$")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“(\[])")
_WORD_RE = re.compile(r"\S+")


@dataclass
class Chunk:
    text: str
    title: str
    section_path: List[str] = field(default_factory=list)
    index: int = 0


def _is_heading(line: str) -> bool:
    raw = line.strip()
    if not raw or len(raw) > 100:
        return False
    # Markdown headings ("# Revenue Analysis") — checked before stripping '#'
    if re.match(r"^#{1,4}\s+\S", raw):
        return True
    line = raw.strip("#").strip()
    if not line:
        return False
    if _HEADING_RE.match(line):
        return True
    # Numbered clause "12.3 Risk factors ..." / "Article IV — ..."
    if re.match(r"^(Article|Section|Clause|Exhibit|Schedule)\b", line, re.I):
        return True
    return False


def chunk_text(
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    source_title: str = "Document",
) -> List[Chunk]:
    """Split content into overlapping, section-aware chunks."""
    if not content or not content.strip():
        return []
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # 1. Group lines into (heading, body) blocks
    blocks: List[Dict] = []
    current_heading = source_title
    section_stack: List[str] = [source_title]
    buf: List[str] = []

    def _flush() -> None:
        if buf:
            blocks.append({"heading": current_heading, "path": list(section_stack), "text": "\n".join(buf).strip()})
            buf.clear()

    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line:
            buf.append("")
            continue
        if _is_heading(line):
            _flush()
            current_heading = line.strip("#").strip()[:100]
            # keep a shallow stack: root + last heading
            section_stack = [source_title] if current_heading == source_title else [source_title, current_heading]
            continue
        buf.append(raw_line.rstrip())
    _flush()
    if not blocks:
        return []

    # 2. Pack blocks into chunks with sentence-level splitting + overlap
    chunks: List[Chunk] = []
    pending = ""
    pending_heading = blocks[0]["heading"]
    pending_path = blocks[0]["path"]

    def _emit(text: str, heading: str, path: List[str]) -> None:
        chunks.append(Chunk(text=text, title=heading, section_path=list(path), index=len(chunks)))

    def _push_piece(piece: str, heading: str, path: List[str]) -> None:
        nonlocal pending, pending_heading, pending_path
        piece = piece.strip()
        if not piece:
            return
        if not pending:
            pending, pending_heading, pending_path = piece, heading, path
            return
        candidate = pending + "\n\n" + piece
        if len(candidate) <= max_chars:
            pending = candidate
            return
        # seal current chunk, carry overlap forward
        _emit(pending, pending_heading, pending_path)
        overlap = pending[-overlap_chars:] if overlap_chars > 0 else ""
        # start next chunk with tail overlap + new piece
        pending, pending_heading, pending_path = ((overlap + "\n" + piece).strip() if overlap else piece), heading, path
        # piece itself may still overflow → hard split by sentences then words
        while len(pending) > max_chars:
            cut = _split_long(pending, max_chars)
            _emit(cut[0], pending_heading, pending_path)
            pending = cut[1]

    for b in blocks:
        sentences = [s.strip() for s in _SENT_RE.split(b["text"]) if s.strip()]
        if not sentences:
            continue
        for sent in sentences:
            if len(sent) > max_chars:
                # word-wrap an oversized sentence
                words, cur = _WORD_RE.findall(sent), ""
                for w in words:
                    if len(cur) + len(w) + 1 > max_chars:
                        _push_piece(cur, b["heading"], b["path"])
                        cur = w
                    else:
                        cur = (cur + " " + w).strip()
                if cur:
                    _push_piece(cur, b["heading"], b["path"])
            else:
                _push_piece(sent, b["heading"], b["path"])
    if pending.strip():
        _emit(pending.strip(), pending_heading, pending_path)
    return chunks


def _split_long(text: str, max_chars: int) -> List[str]:
    """Hard split an over-long buffer at a word boundary."""
    if len(text) <= max_chars:
        return [text, ""]
    cut = text.rfind(" ", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return [text[:cut].strip(), text[cut:].strip()]


def summarize(text: str, limit: int = 280) -> str:
    """First-sentences summary (better than blind [:200] cut)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    sentences = _SENT_RE.split(text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    return (out[:limit].rstrip() + "…") if out else text[:limit].rstrip() + "…"
