# Local Second Brain for Obsidian

A local-first automation engine that turns an [Obsidian](https://obsidian.md) vault
into a semantically searchable "second brain" — using only models that run on your
own machine via [Ollama](https://ollama.com). No document content, embeddings, or
queries are ever sent to a cloud AI provider.

It watches your vault in real time, ingests external documents (PDF, Word,
PowerPoint, plain text), extracts and structures their content, embeds and indexes
everything into a local vector store, and — optionally — proposes tags and links
between related notes. A companion Obsidian plugin adds an in-app search box and a
RAG chat, both backed by the same local index.

This repository accompanies a short paper on the design decisions and failure modes
encountered while building it:

📄 **[Building a Private Second Brain: What Breaks When You Keep AI Local, and Why That's the Point](paper/building-a-private-second-brain.pdf)**
(also available as [Markdown](paper/building-a-private-second-brain.md))

It's meant to be read and adapted, not run as-is out of the box on someone else's
machine — file paths, model names, and the daily-schedule mechanism are all things
you'll want to tune to your own setup.

## Why local?

Three reasons drove every design decision here:

1. **Privacy by construction.** Personal and work notes often contain sensitive
   material. Routing them through a third-party AI API means trusting that
   provider's retention policy. Running embeddings and language models locally
   removes that dependency entirely — the trust boundary is your own laptop.
2. **Cost.** Indexing and re-indexing a growing vault, plus generating tags and
   summaries continuously, adds up fast on metered APIs. Local inference is
   effectively free after the one-time cost of downloading the models.
3. **No rate limits, no outages.** The system works offline, and doesn't depend on
   a third party's uptime.

The trade-off is hardware-bound quality and speed (see "Lessons learned" below) —
this project treats that trade-off as a first-class design constraint, not an
afterthought.

## Architecture

```
                     ┌──────────────────────────┐
  cloud drives  ───▶ │ index_external_folders.py│──▶ Notes/  (read-only ingestion,
  (shared, no        │  (daily, no copying)     │           no duplication)
  cloning wanted)    └──────────────────────────┘
                                                          │
  manually dropped                                        ▼
  documents      ───▶ Sources/ ──▶ watcher.py ──▶ text extraction (PDF/DOCX/PPTX)
  (PDF/DOCX/PPTX)                     │                    │
                                      │                    ▼
                                      │              Notes/*.md  (generated notes)
                                      │                    │
                                      ▼                    ▼
                              chunking + Ollama      tag/link suggestions
                              embeddings  ──▶  Chroma  (Ollama LLM, appended
                                (nomic-embed-text)      inline for generated
                                      │                 notes; kept separate
                                      ▼                 for hand-written ones)
                              search.py / Obsidian plugin
                                (semantic search)

  digest.py (daily) ──▶ Reviews/YYYY-MM-DD.md   (LLM-written summary of the day's changes)
```

### Components

- **`watcher.py`** — a long-running process that watches the vault, converts source
  documents dropped into `Sources/<project>/` into notes, keeps the vector index in
  sync, and generates tag/link suggestions.
- **`index_external_folders.py`** — indexes external folders (cloud drives, shared
  libraries) **without cloning them**: it reads files in place, extracts text, and
  writes only the resulting note. Meant for scheduled (e.g. daily) runs against
  folders you don't want to duplicate or watch in real time.
- **`digest.py`** — writes a daily summary note of what changed in the vault.
- **`search.py`** — one-shot semantic search CLI, also used by the Obsidian plugin.
- **`common.py`** — shared helpers: chunking, Ollama calls, document extraction,
  version/format deduplication.
- **`obsidian-plugin/`** — a minimal Obsidian plugin exposing an in-app search box
  backed by `search.py`, and a chat modal backed by `chat.py --json` (same
  one-shot-subprocess pattern; the plugin keeps conversation history between
  messages so `chat.py` itself stays stateless per call).
- **`eval/eval_search.py`** — a small retrieval-quality harness: runs a set of
  (query, expected note) pairs through the same search path a user experiences, and
  reports Precision@1/@3/@5 and mean reciprocal rank. Copy
  `eval/golden_queries.example.json` to `eval/golden_queries.json` (gitignored) and
  fill it in with real questions and notes from your own vault.
- **`chat.py`** — conversational RAG on top of the same index: ask a question, get a
  synthesized answer with cited sources (the sources are determined by code from
  what was actually retrieved, not by the model), and ask follow-ups that keep the
  conversation's context. Complements `search.py`, which returns raw passages instead.
- **`benchmarks/quantization_bench.py`** — compares speed/quality of the same model
  at different quantization levels (Q4/Q8/fp16) on the same real task, on your own
  hardware. See "Lessons learned" for what this found on a CPU-only laptop.
- **`redteam/prompt_injection_test.py`** — checks that a "malicious" source document
  (text aimed at the model rather than a human) can't make the tag/link pipeline write
  something misleading into a note. See "Lessons learned" for what it found.
- **`finetune/`** — prepares your own vault's notes into a dataset and runs a light
  LoRA fine-tune (MLX, Apple Silicon only) to adapt a small local model's writing style
  to your corpus. Ships with a synthetic example dataset (`finetune/data.example/`) so
  the mechanism can be tried without a real vault. See "Lessons learned" for what this
  found on a small, real note collection.

### Models used (all via Ollama, all local)

- `nomic-embed-text` — embeddings
- `llama3.2` (Q4_K_M) — tag/link suggestions, the daily digest, and the RAG chat

Both are small enough to run comfortably on a laptop CPU. See "Lessons learned" for
why larger/multimodal models were tried and abandoned for parts of this pipeline, and
why Q4_K_M specifically was kept as the default rather than a higher-precision variant.

### Scanned PDFs (OCR)

PDFs with no text layer (scanned documents, photographed pages) fall back to local
OCR automatically via `pytesseract`. This needs the `tesseract` binary installed
separately (`brew install tesseract tesseract-lang` on macOS, `apt install
tesseract-ocr tesseract-ocr-<lang>` on Linux) — if it's missing, those pages are
simply left without text, no hard failure. Set `OCR_LANGUAGES` in `common.py` (e.g.
`"eng+ita"`) to match the languages you actually need; each language requires its
own installed tessdata file.

### Audio recordings (meeting transcripts)

Supported formats: `.mp3`, `.m4a`, `.wav`, `.mp4`, `.aac`, `.flac` — same `Sources/`
flow as any other document, no extra configuration. Transcription uses local Whisper
(`faster-whisper`, `small` model, CPU with int8 quantization — see
`WHISPER_MODEL_SIZE`/`AUDIO_LANGUAGE` in `common.py`); each segment becomes a
timestamped line (`**[MM:SS]**`) so you can navigate a long recording without
re-listening to it. `AUDIO_LANGUAGE` defaults to `None` (auto-detect); set it to a
fixed language code to skip detection and speed things up when you already know what
to expect.

Tested end-to-end with synthetic speech (macOS `say`, meeting-style sentences):
transcription, indexing, and tag/link suggestions all work exactly as they would for
a PDF or DOCX. Not tested against a real meeting recording (multiple speakers,
background noise, overlapping speech) — expect lower transcription quality there than
with clean synthetic audio.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp external_folders_config.example.py external_folders_config.py  # if you want external-folder indexing
```

Set `SECOND_BRAIN_VAULT` (defaults to `~/Documents/SecondBrain`) and
`SECOND_BRAIN_HOME` (defaults to `~/.second-brain`) if you want them elsewhere.
`OLLAMA_URL` defaults to `http://localhost:11434`.

Run the watcher in the foreground to try it out:

```bash
./venv/bin/python watcher.py
```

For always-on background operation, wrap `watcher.py`, `digest.py`, and (if used)
`index_external_folders.py` in your OS's service manager (launchd on macOS, systemd
on Linux, Task Scheduler on Windows) — this repo doesn't ship service unit files
since paths and scheduling preferences are inherently personal.

To build the Obsidian plugin:

```bash
cd obsidian-plugin
npm install
npm run build   # produces main.js
```

Copy the `obsidian-plugin/` folder into `<your-vault>/.obsidian/plugins/second-brain-search/`,
then enable it from Obsidian's Community Plugins settings.

## Lessons learned

These are the design decisions this project got wrong on the first try, kept here
because the failure mode is more instructive than the fix.

- **Local vision models are not ready for slide/diagram captioning.** An early
  version extracted embedded images from decks and captioned them with a small
  local vision model (`moondream`) to make diagrams searchable. In practice the
  model returned empty output for non-English prompts, and even in English it
  confidently hallucinated content on business diagrams (describing an
  architecture diagram as "a close-up of nerves in a brain"). A wrong caption
  silently indexed as fact is worse than no caption — the feature was removed
  entirely rather than shipped as "mostly working." Images are now left out of
  the index; the diagram itself is simply not represented as text.
- **Never trust a model to reproduce a file path.** An earlier version asked the
  LLM to write out full Obsidian wikilinks as free text. It occasionally invented
  paths that didn't exist, or merged two real paths into one that looked
  plausible but pointed nowhere. The fix: show the model a *numbered* list of
  candidate related notes and ask it to pick indices, then have the code build
  the actual link from the known, correct path. The model chooses; the code
  writes.
- **Two notes can legitimately share a filename.** Documents from different
  projects sometimes carry the same filename in different folders. A short-form
  wikilink is ambiguous in that case — Obsidian can't tell which note you mean.
  The system now detects this collision and automatically falls back to a
  fully-qualified path only when needed, keeping links short everywhere else.
- **A parallel "metadata" folder tree doubles the file count and the cognitive
  load.** An earlier design mirrored every note with a matching file in a
  separate `_Suggestions/` folder, so tags and links never touched the original.
  For hand-written notes that caution is warranted. But for notes the pipeline
  itself generated from a source document, there's no user content to protect —
  so those suggestions are now appended directly to the note, guarded by a
  content hash to avoid a rewrite loop, cutting the visible file count roughly in
  half.
- **Removing redundant duplicates needs a policy, not vigilance.** A folder
  synced from multiple contributors accumulates near-duplicates: `report_v01.pptx`,
  `report_v03.pptx`, `report_vFINAL.pptx`, plus a lighter `report_v03.pdf` export
  of the same deck. Two simple, composable rules — keep the highest version
  number (`vFINAL` always wins), then prefer a PDF over an Office file with the
  same name — turned out to cover the overwhelming majority of real-world
  clutter, and are cheap to apply automatically every time new files appear.
- **"Local" storage claims need to be checked, not assumed.** Partway through
  this project, checking `~/Documents` revealed it was a symlink into a
  cloud-drive sync folder set up by the OS-level "back up your folders"
  feature — meaning every file written to the vault had been silently
  syncing to a cloud account the whole time. The AI *processing* was local;
  the *storage* wasn't, and those are two different guarantees that are easy to
  conflate. Worth an explicit check before claiming "nothing leaves this
  machine."
- **A daily-cadence, read-only ingestion pass is often the right answer for
  shared/external content** — not because real-time file watching is technically
  hard, but because a folder you don't own (a shared drive, a colleague's
  export) doesn't need instant reactivity, and treating it read-only (no
  copying, no deletion of the archive if a file disappears upstream) is the
  conservative default that avoids surprises.
- **A retrieval-quality harness finds real ambiguity, not just bugs.** The first
  run of `eval/eval_search.py` against a real note collection scored a modest
  Precision@1 (33%), and the interesting part wasn't the number — it was *which*
  queries failed: two pairs of genuinely different documents about the same
  underlying event sometimes outrank each other. That's a property of the
  embedding model and the data, not something to "fix," but it's much better to
  know it's there than to find out anecdotally.
- **A second failed vision model is more useful than a successful one would have
  been.** The image-captioning experiment (see above) was deliberately repeated
  against a different, similarly-sized local vision model (`llava-phi3`) on the
  same real images. It didn't do better — it hallucinated a different but equally
  confident misreading of the same abstract diagram, and produced degenerate
  output (not just an empty string) when prompted in Italian. Two independent
  models failing in related ways is stronger evidence of a genuine capability
  boundary than one model failing once.
- **Higher precision isn't automatically better precision for a small structured
  task.** Benchmarking `llama3.2` at Q4_K_M/Q8_0/fp16 on this hardware showed the
  expected, large speed gap (Q4 roughly 3x faster than fp16 in tokens/sec) but no
  clearly monotonic quality improvement on the tag/link-suggestion task specifically
  — fp16 was not obviously better than Q4 on that one job. The default stayed at
  Q4_K_M: it was already the fastest, and the benchmark didn't surface a quality
  reason to trade that away.
- **A JSON schema is a cheaper fix than a better prompt.** The tag/link prompt
  originally asked the model to format its answer as two labeled free-text lines,
  parsed with string matching. Ollama's native structured-output support
  (`format` as a JSON schema) removes that parsing step entirely — the model
  fills in fields, the code reads them directly. This doesn't replace the
  numbered-candidate-list safeguard above (the model still can't invent a path,
  it can only pick a valid index), but it does remove a second, smaller class of
  failure: the model almost-but-not-quite following a free-text format.
- **A schema constrains shape, not content — and the gap is exploitable.** The JSON
  schema above stops the model from inventing a malformed response, but nothing
  stopped a *tag string itself* from containing `[[a wikilink]]` or an embedded
  newline. `redteam/prompt_injection_test.py` planted an injected instruction inside
  a source document asking the model to emit exactly such a tag. Tested in English
  against `llama3.2`, the model **complied** — it produced the literal string
  `[[Confidential CEO Data]]` as a tag, which would have rendered as a real, clickable
  Obsidian link had it reached the note. The same test in Italian didn't convince the
  model, which is itself the point: model compliance with an injection is
  probabilistic and inconsistent across languages, so it can't be the only defense.
  `_sanitize_tag()` in `watcher.py` now rejects any tag containing wikilink brackets,
  newlines, or the suggestions-marker sequence before it's ever written — a
  content-level check the schema alone couldn't provide. Its tag count is also capped
  (`maxItems: 10` in the schema, plus the identical cap in code — again, never trust
  the schema alone) so a misbehaving model can't bloat a note with hundreds of tags.
  The same two injected documents were also tested against the RAG chat (`chat.py`):
  in both cases the model retrieved the malicious note as context but explicitly
  recognized the embedded instruction as something to ignore rather than follow —
  encouraging, but observed behavior, not a structural guarantee the way tag
  sanitization is (chat output is free text shown to the user, not something the code
  parses and writes into a file, so there's no equivalent code-level backstop here).
- **A symlink inside `Sources/` silently read whatever it pointed to.** A concrete test
  (a symlink placed inside `Sources/` pointing at a file with fake "secret" content
  outside the vault entirely) showed the pipeline extracting, indexing, and making that
  content searchable and chattable — exactly as if the user had deliberately dropped it
  in. `Path.read_text()` and the other extraction functions follow symlinks
  transparently by default; nothing in the code checked for that. `convert_source()`
  now rejects any path whose resolved location doesn't actually fall under `Sources/`
  before reading it, closing the gap for both single-file and whole-directory symlinks
  — verified again after the fix. This matters most for synced/shared folders (cloud
  drives, shared libraries) where a collaborator, or a malformed archive extraction,
  could introduce a symlink like this without you noticing.
- **A LoRA fine-tune mostly learns the pipeline's formatting conventions before it
  learns a personal voice.** `finetune/` fine-tuned a small local model (LoRA, MLX, on
  Apple Silicon) on a real note collection — 13 notes, about 165KB of text, split into
  214/26/26 train/valid/test records. Training genuinely worked: validation loss
  dropped consistently at every checkpoint (from 3.60 to 2.10 over 200 iterations),
  only 0.216% of the model's parameters were touched (a 28MB adapter file, not a
  duplicated model), and the whole run took about 13 minutes on a consumer laptop. But
  the most consistent qualitative change wasn't a personal "writing style" — it was the
  model reproducing the *ingestion pipeline's own markdown conventions* (numbered slide
  headers, short bullet-style fragments) more than any authorial voice, which makes
  sense once you notice most of the corpus is auto-extracted business documents, not
  personal reflective writing. The effect was also inconsistent depending on how the
  model was prompted: it showed up clearly with raw text completion (the format
  actually used for training) and mostly disappeared when prompted through the
  instruct model's normal chat template — a training/inference format mismatch that
  limits how much the adapter generalizes to everyday chat use. None of this is a
  failure of the technique; it's a direct consequence of fine-tuning on a genuinely
  small, auto-extracted corpus, which is precisely the caveat worth recording rather
  than glossing over.

## What this repository deliberately does not include

- Any actual vault content, extracted document text, or personal configuration —
  this is the engine, not a dataset.
- OS-level service files (launchd/systemd) — these encode personal paths and
  scheduling preferences.
- A hosted or one-click deployment — this is meant to be read, adapted, and run on
  your own machine, not operated as a service.

## License

MIT — see `LICENSE`.
