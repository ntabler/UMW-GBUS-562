# Week 3: Copperline RAG Tool

`ask.py` is a command-line tool that answers questions from Copperline Manufacturing's finance documents using retrieval-augmented generation (RAG). It finds the 4 most relevant chunks in a local Chroma database, then asks Claude to answer from those chunks only and to cite the source file for each fact.

## Setup

From the top of the repository:

```
.venv/bin/python -m pip install -r week-03/requirements.txt
```

The tool reads the API key at runtime from `API-Key.txt` in the folder that contains the repository (or from `ANTHROPIC_API_KEY`, if that is set). The key is never stored in this repository.

## Usage

Run these from the top of the repository:

```
.venv/bin/python week-03/ask.py 'How much can I spend on dinner in Boston?'
.venv/bin/python week-03/ask.py
.venv/bin/python week-03/ask.py --rebuild
.venv/bin/python week-03/ask.py --docs week-03/larkfield-docs 'What is the overdraft fee?'
.venv/bin/python week-03/ask.py 'What is the hotel limit in Boston?' --save week-03/runs/boston.txt
```

- The first command answers one question. Use single quotes, because in double quotes the shell removes dollar amounts such as `$2,500`.
- The second keeps asking at a prompt until you press Enter on an empty line.
- The third rebuilds the database. Run it after adding, removing, or editing documents. The tool warns you if the documents have changed since the last build.
- `--docs` names the document folder to answer from. Without it, `week-03/copperline-docs` is used. Add `--docs` to `--rebuild` too, to rebuild that folder's database.
- `--save` also writes the output to a UTF-8 text file: the retrieved chunks, then a line containing only `ANSWER`, then the answer. It needs a question on the command line, and it replaces any file already at that path.

Each document folder has its own database, built automatically the first time you ask about that folder. The databases are stored in `week-03/chroma_db/` and are not committed to Git.

## Documents

`copperline-docs/` holds Copperline Manufacturing's finance documents, and `larkfield-docs/` holds Larkfield Credit Union's member services documents. HTML and PDF files are supported. A rebuild lists any file it could not read, including scanned PDFs, which contain no text. To add another file type, write a parser and add it to `PARSERS` in `ask.py`.

`copperline-docs-archive/` holds a superseded version of the travel policy. It is deliberately left out of the database so that answers never use outdated rules.

## How chunking works

Each section under a heading becomes one chunk, so that lists, procedures, and FAQ answers stay whole. Every chunk starts with its document's title and section heading, and records its source file. Sections too long for the embedding model are split into parts that repeat the title and heading.

Two definitions appear in only one section of their document, so they are copied into the other sections that depend on them: the city tiers (needed by Hotels) and the business day numbering (needed by the close calendar deadlines). These are set in `CONTEXT_RULES` in `ask.py`.
