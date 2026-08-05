# Cortana

A local, self-improving chat assistant that runs entirely inside a
Chromebook's Linux (Crostini) container. It uses [Ollama](https://ollama.com)
to run a small quantized model on-device, and grows a local knowledge base
over time by researching the web — including on its own, unprompted.

## How "self-improve" works here

Chromebooks don't have a usable GPU and Crostini doesn't expose compute
acceleration to the Linux VM, so this project does **not** retrain the
model's weights on-device (that would be far too slow to be usable). Instead
it improves in a different, fully local way — it accumulates knowledge:

1. It researches a topic: searches the web, fetches a handful of pages, and
   asks the local model to distill each one into a concise note.
2. Notes are embedded and stored in a local SQLite database (`data/memory.db`).
3. On every future chat turn, it retrieves the most relevant notes from that
   database and feeds them to the model as context (retrieval-augmented
   generation, aka RAG).

The model's weights never change, but its effective knowledge grows, and it
remembers across sessions and reboots.

There are three ways research gets triggered, and only the first needs you:

| Trigger | Needs you? | What happens |
|---|---|---|
| `/learn <topic>` | Yes | Researches that topic immediately. |
| Mid-chat | No | If Cortana doesn't know something while answering, she searches the web herself, saves notes, then answers — and tells you what she looked up. |
| Background daemon | No | Runs on a timer with no conversation at all. Researches a topic, asks the model what's worth exploring next, queues those, repeats. Refills its own queue when it runs dry. |

If you later want actual weight fine-tuning, the notes in `data/memory.db`
are a ready-made dataset to ship to a cloud GPU for a LoRA run. That's out of
scope here.

## Requirements

- A Chromebook with Linux (Crostini) enabled:
  Settings → Advanced → Developers → Linux development environment.
- ~4GB free disk space for the model (more if you let the daemon run for
  months — see *Disk growth* below).
- Works on both Intel/AMD and ARM Chromebooks.

## Setup

```bash
git clone <this repo> cortana
cd cortana
./setup.sh
```

`setup.sh` handles the things that actually bite on a fresh Crostini container:

- Installs `python3-venv`, `python3-pip`, and `curl` — Debian ships `python3`
  **without** the venv module, so `python3 -m venv` fails until you do this.
- Detects your RAM and picks a model that fits (see below).
- Creates the virtualenv and installs dependencies.
- Installs Ollama, starts the server if it isn't running (Crostini doesn't
  reliably run it as a systemd service), and pulls the models.

The Python environment is set up *before* anything that needs the Ollama
server, so a model-pull failure still leaves you with a working environment.

### Model sizing

Chromebook RAM varies a lot, and an oversized model swap-thrashes the VM.
`setup.sh` picks automatically:

| Detected RAM | Model | Approx. size |
|---|---|---|
| under 4GB | `llama3.2:1b` | ~1.3GB |
| 4–8GB | `gemma2:2b` | ~1.6GB |
| 8GB+ | `llama3.2:3b` | ~2GB |

Detection reads `MemTotal`, which reports what the Crostini VM actually got —
a few percent under the Chromebook's nominal RAM — and rounds down. So a
nominal 8GB Chromebook lands on the 2B model. That bias is deliberate:
a smaller model that responds promptly beats a bigger one that thrashes.

Override any time with `export CHAT_MODEL=llama3.2:3b`.

## Usage

```bash
source .venv/bin/activate
python -m llm_agent.cli
```

In the REPL:

- Type anything to chat. Relevant saved notes are pulled in automatically,
  and Cortana may research the web on her own mid-answer.
- `/learn <topic>` — research a topic right now.
- `/curious <topic>` — queue a topic for the background daemon.
- `/queue` — show what the daemon has waiting.
- `/memory` — how many notes are stored, plus the most recent.
- `/help`, `/exit`.

## Autonomous background research

To have Cortana keep learning with no interaction at all, run the daemon in
another terminal tab (or under `nohup`/cron/systemd):

```bash
source .venv/bin/activate
python -m llm_agent.daemon "topic one" "topic two"   # seed topics optional
```

Each cycle it pops a pending topic, researches it, saves notes, asks the
model which related topics are worth exploring next, and queues those.

**It won't stall.** If a research pass turns up only things it already knows,
it produces no follow-ups — and since completed topics are never re-queued,
the queue could otherwise drain permanently. When that happens the daemon
asks the model to propose entirely new directions based on what's already in
memory, so it keeps going indefinitely. On a cold start with an empty queue
it seeds itself from `SEED_TOPICS`.

It's bounded on purpose, since nothing supervises it turn by turn:

- `CURIOSITY_INTERVAL_SECONDS` (default 1800 = 30 min) paces research.
- `CURIOSITY_MAX_QUEUE_SIZE` (default 50) caps pending topics.
- `CURIOSITY_FOLLOW_UPS_PER_TOPIC` (default 2) caps topics spawned per pass.
- Self-replenishment fires at most once per drain, not once per poll.

Every action is logged with a timestamp so you can see exactly what it did
while you weren't watching. Stop it any time with Ctrl+C.

The CLI and daemon can run simultaneously — the database uses WAL mode so
`/curious` works while the daemon is mid-write.

### Disk growth

Each note costs ~17KB, mostly the embedding vector. Running the daemon
non-stop at defaults is roughly **3MB/day (~1.1GB/year)** worst case. If
that matters on your Chromebook, raise `CURIOSITY_INTERVAL_SECONDS` or lower
`SEARCH_RESULTS`. Exact-duplicate notes are discarded, so restating the same
fact doesn't accumulate.

## Configuration

All settings are environment variables (see `llm_agent/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_NAME` | `Cortana` | What the assistant calls itself |
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama's API is running |
| `CHAT_MODEL` | auto-detected | Model used for conversation |
| `EMBED_MODEL` | `nomic-embed-text` | Model used to embed notes |
| `MEMORY_DB_PATH` | `data/memory.db` | SQLite database path |
| `MEMORY_TOP_K` | `4` | Notes retrieved per chat turn |
| `SEARCH_RESULTS` | `4` | Web results fetched per research pass |
| `MAX_FETCH_CHARS` | `6000` | Page text fed to the model |
| `CURIOSITY_INTERVAL_SECONDS` | `1800` | Seconds between research cycles |
| `CURIOSITY_IDLE_POLL_SECONDS` | `60` | Poll rate while the queue is empty |
| `CURIOSITY_MAX_QUEUE_SIZE` | `50` | Max pending topics |
| `CURIOSITY_FOLLOW_UPS_PER_TOPIC` | `2` | Max topics spawned per pass |
| `SEED_TOPICS` | 3 defaults | Semicolon-separated cold-start topics; `""` disables |
| `DB_BUSY_TIMEOUT_SECONDS` | `30` | How long a writer waits for the DB lock |

## Project layout

```
llm_agent/
  config.py       settings (env-var driven)
  llm_client.py   wrapper around Ollama's chat/embeddings API
  memory.py       SQLite-backed vector store (notes, cosine search, dedup)
  web_search.py   DuckDuckGo search + page text extraction, no API key
  agent.py        RAG chat, research, follow-up and new-topic proposals
  topics.py       persistent queue of topics for the daemon
  daemon.py       autonomous loop: research, queue follow-ups, self-replenish
  cli.py          interactive REPL
tests/            62 tests, no network or Ollama required
```

## Testing

```bash
source .venv/bin/activate
pip install pytest && python -m pytest tests/ -q
```

The suite is hermetic — the LLM and all network calls are stubbed, so it
runs without Ollama installed and without an internet connection.

## Known limitations

- **Search is scraping, not an API.** It uses DuckDuckGo's no-JavaScript
  HTML endpoint, which needs no API key but may break if their markup
  changes. `web_search.search()` returns `[]` on failure rather than
  crashing, so a break degrades to "finds nothing" rather than an error.
- **Memory search is a linear scan.** All embeddings are loaded and compared
  on every search. Fine for hundreds to low thousands of notes; it is not
  built to scale past that.
- **Notes are only as good as the model writing them.** A 1–3B model
  summarizing a page will sometimes be vague or wrong, and a wrong note
  becomes context for future answers. Sources are stored alongside every
  note (`/memory`) so you can check them.
- **Only exact duplicates are deduped.** Two differently-worded notes saying
  the same thing will both be stored.
- **No weight fine-tuning**, by design (see above).
