"""Interactive REPL for the Chromebook LLM agent."""

from . import config
from .agent import Agent
from .llm_client import OllamaError
from .memory import Memory
from .topics import TopicQueue

HELP_TEXT = """\
Commands:
  /learn <topic>   Research a topic on the web right now and save what's learned.
  /curious <topic> Queue a topic for the background daemon to research later,
                   with no further input from you (run it separately with:
                   python -m llm_agent.daemon).
  /queue           Show topics waiting for the background daemon.
  /memory          Show how many notes are stored and the most recent ones.
  /help            Show this message.
  /exit            Quit.

Anything else is sent to Cortana as a chat message. She'll pull in relevant
saved notes automatically, and may research the web on her own if she
doesn't know the answer -- when she does, she'll tell you what she searched.
"""

MAX_HISTORY_TURNS = config.MAX_HISTORY_TURNS


def _print_learn_result(result) -> None:
    # "Couldn't reach the web" and "searched but found nothing" call for
    # different responses from the user, so don't report them identically.
    if not result.reachable:
        print(
            f"Couldn't reach the web to research '{result.topic}'. "
            "Check your connection and try again."
        )
        return
    if not result.notes_added:
        print(f"Searched, but found nothing usable for '{result.topic}'.")
        return
    print(f"Learned {len(result.notes_added)} note(s) about '{result.topic}':")
    for url, note in result.notes_added:
        preview = note[:140] + ("..." if len(note) > 140 else "")
        print(f"  - {url}\n    {preview}")


def main() -> None:
    print(
        f"{config.ASSISTANT_NAME} -- local chat with a growing memory. "
        "Type /help for commands."
    )
    print(f"(chat model: {config.CHAT_MODEL}, embed model: {config.EMBED_MODEL})\n")

    memory = Memory()
    agent = Agent(memory=memory)
    queue = TopicQueue()
    history = []

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            break

        if user_input == "/help":
            print(HELP_TEXT)
            continue

        if user_input == "/memory":
            print(f"{memory.count()} note(s) stored.")
            for note in memory.recent(5):
                preview = note.content[:100] + ("..." if len(note.content) > 100 else "")
                print(f"  [{note.id}] {note.topic} ({note.created_at})\n      {preview}")
            continue

        if user_input.startswith("/curious"):
            topic = user_input[len("/curious"):].strip()
            if not topic:
                print("Usage: /curious <topic>")
                continue
            if queue.add(topic):
                print(
                    f"Queued '{topic}' for the background daemon. Run it with: "
                    "python -m llm_agent.daemon"
                )
            else:
                print(f"'{topic}' is already queued (or the queue is full).")
            continue

        if user_input == "/queue":
            pending = queue.pending()
            if not pending:
                print("Queue is empty.")
            else:
                print(f"{len(pending)} topic(s) waiting for the daemon:")
                for t in pending:
                    print(f"  [{t.id}] {t.topic}")
            continue

        if user_input.startswith("/learn"):
            topic = user_input[len("/learn"):].strip()
            if not topic:
                print("Usage: /learn <topic>")
                continue
            try:
                result = agent.learn(topic)
            except OllamaError as exc:
                print(f"Error: {exc}")
                continue
            _print_learn_result(result)
            continue

        try:
            result = agent.chat(user_input, history=history)
        except OllamaError as exc:
            print(f"Error: {exc}")
            continue

        if result.researched_query:
            print(f"[researched: \"{result.researched_query}\" -> {result.notes_added} note(s) saved]")

        print(result.answer)

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": result.answer})
        history[:] = history[-MAX_HISTORY_TURNS * 2:]

    memory.close()
    queue.close()


if __name__ == "__main__":
    main()
