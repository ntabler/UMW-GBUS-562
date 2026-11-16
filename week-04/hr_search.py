"""Search one organization's HR documents with a Chroma database.

The reading, chunking, and search steps are copied from week-03/ask.py, with
three changes so the code can run inside an MCP server:

- Progress messages go to stderr, never stdout. A stdio MCP server uses stdout
  to talk to the AI app, so a stray print would break the connection.
- Problems raise SearchError instead of ending the program.
- When the documents change, the database rebuilds itself instead of printing a
  warning, because nobody is watching a server's output.

The Claude call, PDF parser, and Copperline-specific context rules from ask.py
are left out: the server returns matching text, and the AI app writes the reply.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
import chromadb.errors
from bs4 import BeautifulSoup, NavigableString, Tag
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

import org as org_module

# Set by configure(). The default is week-04/hartwell-hr. Each organization has
# its own database folder under week-04/chroma_db/, which Git ignores.
_DEFAULT_ORG = org_module.Org(org_module.DEFAULT_ORG_DIR)
DOCS_DIR = _DEFAULT_ORG.docs_dir
DB_DIR = _DEFAULT_ORG.db_dir

COLLECTION = "documents"
TOP_K = 4

# Chroma's built-in model reads only the first 256 tokens of a text, which is
# about 190 words. Longer sections are split so that nothing goes unsearched.
MAX_CHUNK_WORDS = 170
# Text before the first heading this short is a version line, so it is stored
# as a detail. Anything longer is real content and becomes a chunk.
MAX_INFO_WORDS = 40


class SearchError(Exception):
    """A problem with the documents or the database, worded for the AI app."""


def configure(org: org_module.Org) -> None:
    """Search this organization's documents, with its own database."""
    global DOCS_DIR, DB_DIR
    DOCS_DIR = org.docs_dir
    DB_DIR = org.db_dir


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class Section:
    heading: str
    blocks: list[str] = field(default_factory=list)


@dataclass
class Document:
    title: str
    info: str = ""  # Version, owner, and similar details.
    sections: list[Section] = field(default_factory=list)


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    title: str
    section: str
    info: str


def clean(text: str) -> str:
    return " ".join(text.split())


def word_count(text: str) -> int:
    return len(text.split())


def title_from(raw: str | None, path: Path) -> str:
    """Drop the ' | Company' suffix, falling back to the file name."""
    title = clean(re.split(r"\s+\|\s+", raw or "")[0])
    return title or path.stem.replace("-", " ").replace("_", " ").title()


def assemble(title: str, preamble: list[str], sections: list[Section]) -> Document:
    """Sort the text found before the first heading into details or content."""
    preamble = [text for text in preamble if clean(text) != title]
    sections = [section for section in sections if section.blocks]
    info = clean(" ".join(preamble))
    # A document with no headings keeps all its text here, however short.
    if preamble and (not sections or word_count(info) > MAX_INFO_WORDS):
        heading = "Introduction" if sections else "Full text"
        sections = [Section(heading, preamble), *sections]
        info = ""
    return Document(title, info, sections)


# --- HTML -------------------------------------------------------------------

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
BLOCK_TAGS = (*HEADING_TAGS, "p", "ul", "ol", "table", "pre", "blockquote")


def list_text(tag: Tag) -> str:
    items = [clean(li.get_text(" ")) for li in tag.find_all("li", recursive=False)]
    return "\n".join(f"- {item}" for item in items if item)


def table_text(tag: Tag) -> str:
    rows = []
    for row in tag.find_all("tr"):
        cells = [clean(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def parse_html(path: Path) -> Document:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    page_title = soup.title.get_text() if soup.title else ""
    first_h1 = soup.find("h1")
    title = title_from(page_title or (first_h1.get_text() if first_h1 else ""), path)
    # Footers (in the sample organizations, only a note that the company is fictional) are dropped.
    for tag in soup(["script", "style", "noscript", "template", "nav", "footer"]):
        tag.decompose()

    preamble: list[str] = []
    sections: list[Section] = []
    heading_path: dict[int, str] = {}

    def add(text: str) -> None:
        if text:
            (sections[-1].blocks if sections else preamble).append(text)

    def walk(node: Tag) -> None:
        for child in node.children:
            # Comments and doctypes are subclasses, so this must be an exact check.
            if type(child) is NavigableString:
                add(clean(child))
            elif not isinstance(child, Tag):
                continue
            elif child.name in HEADING_TAGS:
                if child is first_h1:
                    continue
                level = int(child.name[1])
                heading_path[level] = clean(child.get_text(" "))
                for deeper in [lvl for lvl in heading_path if lvl > level]:
                    del heading_path[deeper]
                # Keep a subheading's parent so it still makes sense alone.
                sections.append(Section(" > ".join(heading_path[k] for k in sorted(heading_path))))
            elif child.name in ("ul", "ol"):
                add(list_text(child))
            elif child.name == "table":
                add(table_text(child))
            elif child.name in BLOCK_TAGS or not child.find(BLOCK_TAGS):
                add(clean(child.get_text(" ")))
            else:
                walk(child)

    walk(soup.find("main") or soup.body or soup)
    return assemble(title, preamble, sections)


PARSERS = {".html": parse_html, ".htm": parse_html}


# --- Chunking ---------------------------------------------------------------


def split_block(block: str, limit: int) -> list[str]:
    """Break one long block at list items, then sentences, then words."""
    if word_count(block) <= limit:
        return [block]
    pieces = block.split("\n") if "\n" in block else re.split(r"(?<=[.!?])\s+", block)
    if len(pieces) > 1:
        return [part for piece in pieces for part in split_block(piece, limit)]
    words = block.split()
    return [" ".join(words[i : i + limit]) for i in range(0, len(words), limit)]


def pack_blocks(blocks: list[str], limit: int) -> list[str]:
    """Group blocks into parts under the limit, keeping lead-ins with their lists."""
    parts: list[list[str]] = [[]]
    for block in (piece for block in blocks for piece in split_block(block, limit)):
        current = parts[-1]
        if current and word_count(" ".join([*current, block])) > limit:
            # A line ending in a colon introduces what follows, so it moves too.
            carried = [current.pop()] if current[-1].endswith(":") and len(current) > 1 else []
            parts.append(carried)
        parts[-1].append(block)
    return ["\n\n".join(part) for part in parts if part]


def document_chunks(doc: Document, source: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        reserved = word_count(f"{doc.title} {section.heading} (part 10 of 10)")
        parts = pack_blocks(section.blocks, MAX_CHUNK_WORDS - reserved)
        for number, body in enumerate(parts, 1):
            heading = section.heading
            if len(parts) > 1:
                heading = f"{heading} (part {number} of {len(parts)})"
            text = f"{doc.title}\n{heading}\n\n{body}"
            chunks.append(Chunk(f"{source}::{len(chunks)}", text, source, doc.title, heading, doc.info))
    return chunks


def document_files() -> list[Path]:
    """Every file under the documents folder, leaving out hidden ones."""
    return sorted(
        path
        for path in DOCS_DIR.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(DOCS_DIR).parts)
    )


def fingerprint() -> str:
    """A value that changes when any document is added, removed, or edited."""
    digest = hashlib.sha256()
    for path in document_files():
        digest.update(path.relative_to(DOCS_DIR).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_chunks() -> list[Chunk]:
    if not DOCS_DIR.is_dir():
        raise SearchError(f"The HR documents folder was not found: {DOCS_DIR}")

    chunks: list[Chunk] = []
    for path in document_files():
        source = path.relative_to(DOCS_DIR).as_posix()
        parser = PARSERS.get(path.suffix.lower())
        if parser is None:
            log(f"  WARNING: {source}: skipped, {path.suffix or 'this file type'} is not supported.")
            continue
        try:
            new_chunks = document_chunks(parser(path), source)
        except Exception as error:  # One unreadable file should not stop the rest.
            log(f"  WARNING: {source}: skipped, could not be read ({error}).")
            continue
        if new_chunks:
            log(f"  {source}: {len(new_chunks)} chunks")
            chunks.extend(new_chunks)
        else:
            log(f"  WARNING: {source}: skipped, no text found.")
    return chunks


# --- Chroma -----------------------------------------------------------------


def chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(DB_DIR), settings=Settings(anonymized_telemetry=False))


def rebuild() -> None:
    """Replace the database with one built from the documents now in the folder."""
    log(f"Reading documents in {DOCS_DIR}")
    chunks = load_chunks()
    if not chunks:
        raise SearchError("No HR documents could be read, so the database was left unchanged.")

    # Embed first: if this fails, the existing database has not been touched.
    log(f"Embedding {len(chunks)} chunks...")
    embedding_function = DefaultEmbeddingFunction()
    embeddings = embedding_function([chunk.text for chunk in chunks])

    if DB_DIR.exists():
        shutil.rmtree(DB_DIR)
    DB_DIR.parent.mkdir(parents=True, exist_ok=True)
    client = chroma_client()
    collection = client.create_collection(
        COLLECTION,
        embedding_function=embedding_function,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"docs_fingerprint": fingerprint()},
    )
    batch_size = client.get_max_batch_size()
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        collection.add(
            ids=[chunk.id for chunk in batch],
            documents=[chunk.text for chunk in batch],
            embeddings=embeddings[start : start + batch_size],
            metadatas=[
                {"source": c.source, "title": c.title, "section": c.section, "info": c.info}
                for c in batch
            ],
        )
    log(f"Database rebuilt: {len(chunks)} chunks in {DB_DIR}")


def open_collection() -> chromadb.Collection:
    """Open the database, building or rebuilding it if it is missing or out of date."""
    collection = None
    if DB_DIR.exists():
        try:
            collection = chroma_client().get_collection(
                COLLECTION, embedding_function=DefaultEmbeddingFunction()
            )
        except chromadb.errors.NotFoundError:
            log("The database is incomplete, so rebuilding it.")
    else:
        log("No database yet, so building it.")

    if collection is not None and (collection.metadata or {}).get("docs_fingerprint") != fingerprint():
        log("The HR documents have changed since the database was built, so rebuilding it.")
        collection = None

    if collection is None:
        rebuild()
        collection = chroma_client().get_collection(
            COLLECTION, embedding_function=DefaultEmbeddingFunction()
        )
    return collection


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """Return the chunks closest in meaning to the query, best first."""
    collection = open_collection()
    result = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "source": metadata["source"],
            "title": metadata["title"],
            "version": metadata["info"],
            "section": metadata["section"],
            # Cosine distance runs from 0 (same meaning) upward, so 1 - distance
            # gives a similarity where higher is better.
            "similarity": round(1 - distance, 3),
            "text": text,
        }
        for text, metadata, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        )
    ]
