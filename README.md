# Chromebook LLM

A local, self-improving chat assistant that runs entirely inside a
Chromebook's Linux (Crostini) container. It uses [Ollama](https://ollama.com)
to run a small quantized model on-device, and grows a local knowledge base
over time by researching the web and saving what it learns.

## How "self-improve" works here

Chromebooks don't have a GPU and Crostini doesn't expose compute
acceleration to the Linux VM, so this project does **not** retrain the
model's weights on-device (that would be far too slow to be usable). Instead
it improves in a different, fully local way:

1. You ask it to research a topic (`/learn <topic>`).
2. It searches the web, fetches a handful of pages, and asks the local LLM
   to distill each one into a concise note.
3. Notes are embedded and stored in a local SQLite database
   (`data/memory.db`).
4. On every future chat turn, it retrieves the most relevant notes from that
   database and feeds them to the model as context (retrieval-augmented
   generation, aka RAG).

So the model's weights never change, but its effective knowledge grows every
time you point it at something new, and it remembers what it learned across
sessions — all offline-capable after the initial research pass.

**It can also research without you asking anything.** During chat, if the
model doesn't know something, it triggers a web search on its own mid-turn
(no command needed) and tells you what it looked up. And there's a separate
background daemon (`llm_agent/daemon.py`) you can leave running that
continuously researches topics on a timer with zero interaction from you: it
picks a topic off a queue, researches it, saves notes, asks the model what
related topics are worth researching next, queues those, and repeats. See
"Autonomous background research" below.

If you later want actual weight fine-tuning, the collected notes in
`data/memory.db` are a ready-made dataset you could ship to a cloud GPU for
a LoRA fine-tune. That's out of scope for this project as-is.

## Requirements

- A Chromebook with Linux (Crostini) enabled: Settings → Advanced → Developers → Linux development environment.
- ~4GB free disk space for the model.
- Python 3.9+ (included by default in Debian/Crostini).

## Setup

```bash
git clone <this repo> chromebook-llm
cd chromebook-llm
./setup.sh
```

`setup.sh` will:
- Install Ollama if it isn't already installed.
- Pull the default chat model (`llama3.2:3b`) and embedding model (`nomic-embed-text`).
- Create a Python virtual environment and install dependencies.

If `ollama serve` isn't already running as a background service, start it in
a separate terminal tab first:

```bash
ollama serve
```

## Usage

```bash
source .venv/bin/activate
python -m llm_agent.cli
```

Then in the REPL:

- Type anything to chat. Relevant saved notes are automatically pulled in as context.
- `/learn <topic>` — research a topic on the web right now and save what's learned to memory.
- `/curious <topic>` — queue a topic for the background daemon to research later, unattended.
- `/queue` — show topics waiting for the background daemon.
- `/memory` — show how many notes are stored and the most recent ones.
- `/help` — list commands.
- `/exit` — quit.

## Autonomous background research

The chat REPL only researches while you're actively talking to it. To have
it keep researching **on its own, with no chat interaction at all**, run the
daemon in a separate terminal tab (or `nohup ... &`, or a cron/systemd job):

```bash
source .venv/bin/activate
python -m llm_agent.daemon "topic one" "topic two"   # seed topics are optional
```

Each cycle it:
1. Pops the next pending topic off a persistent queue (stored in the same
   `data/memory.db`).
2. Researches it the same way `/learn` does, saving notes to memory.
3. Asks the model what related topics are worth researching next, and adds
   those to the queue.
4. Sleeps, then repeats.

You can seed the queue either as command-line arguments to the daemon, or
with `/curious <topic>` from the chat REPL while the daemon isn't running —
it'll pick them up next time it starts (or immediately, if it's already
running and polling the queue between cycles).

This is intentionally bounded so it doesn't run away unsupervised:

- `CURIOSITY_INTERVAL_SECONDS` (default 1800 = 30 min) paces requests.
- `CURIOSITY_MAX_QUEUE_SIZE` (default 50) caps how many topics can be pending.
- `CURIOSITY_FOLLOW_UPS_PER_TOPIC` (default 2) caps how many new topics one
  research pass can spawn.

Every action is logged to stdout with a timestamp, so you can see exactly
what it did while you weren't watching. Stop it any time with Ctrl+C.

## Configuration

All settings are environment variables with sane defaults, see
`llm_agent/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama's API is running |
| `CHAT_MODEL` | `llama3.2:3b` | Model used for conversation |
| `EMBED_MODEL` | `nomic-embed-text` | Model used to embed text for memory search |
| `MEMORY_DB_PATH` | `data/memory.db` | SQLite database path |
| `MEMORY_TOP_K` | `4` | How many notes to retrieve per chat turn |
| `SEARCH_RESULTS` | `4` | How many web results to fetch per `/learn` |
| `MAX_FETCH_CHARS` | `6000` | How much of a fetched page to feed to the model |
| `CURIOSITY_INTERVAL_SECONDS` | `1800` | Seconds between daemon research cycles |
| `CURIOSITY_MAX_QUEUE_SIZE` | `50` | Max pending topics in the daemon's queue |
| `CURIOSITY_FOLLOW_UPS_PER_TOPIC` | `2` | Max new topics spawned per research pass |

Swap `CHAT_MODEL` for something smaller (e.g. `gemma2:2b`, `phi3:mini`) if a
3B model is too slow on your hardware, or larger if you have RAM to spare.

## Project layout

```
llm_agent/
  config.py       settings (env-var driven)
  llm_client.py   thin wrapper around Ollama's chat/embeddings API
  memory.py       SQLite-backed local vector store (notes + cosine search)
  web_search.py   DuckDuckGo search + page text extraction, no API key needed
  agent.py        orchestrates chat (RAG), /learn, and follow-up topic suggestions
  topics.py       persistent queue of topics for the background daemon
  daemon.py       standalone loop: research a topic, queue follow-ups, repeat
  cli.py          interactive REPL
tests/
  test_memory.py  unit tests for the vector store's similarity search
```

## Known limitations

- Web search scrapes DuckDuckGo's HTML endpoint (no API key required) — it's
  best-effort and may break if DuckDuckGo changes their markup.
- Memory search loads all stored embeddings into memory for a cosine-similarity
  scan. That's fine for hundreds to low thousands of notes; it isn't built to
  scale past that.
- No weight fine-tuning, by design (see above).
