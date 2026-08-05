"""Autonomous background research loop.

Run this as a separate process (a spare terminal tab, `nohup ... &`, or a
cron/systemd job) and it will keep researching topics on its own, with no
chat interaction required: it pops a topic off the queue, researches it,
saves notes to memory, asks the model for related follow-up topics, adds
those to the queue, sleeps, and repeats.

It is bounded on purpose, since nothing is supervising it turn by turn:
- CURIOSITY_MAX_QUEUE_SIZE caps how many topics can be pending at once.
- CURIOSITY_FOLLOW_UPS_PER_TOPIC caps how many new topics one research pass can spawn.
- CURIOSITY_INTERVAL_SECONDS paces requests so it doesn't hammer the network or the model.

Every action is logged to stdout with a timestamp so what it did while
unattended is always visible after the fact. Stop it any time with Ctrl+C.
"""

import sys
import time
from datetime import datetime, timezone
from typing import List

from . import config
from .agent import Agent
from .llm_client import OllamaError
from .memory import Memory
from .topics import TopicQueue


def _log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def run_once(agent: Agent, queue: TopicQueue) -> bool:
    """Process one topic from the queue. Returns True if a topic was processed."""
    next_topic = queue.pop_next()
    if next_topic is None:
        return False

    _log(f"researching: {next_topic.topic}")
    try:
        result = agent.learn(next_topic.topic)
    except OllamaError as exc:
        _log(f"error researching '{next_topic.topic}': {exc}")
        return True

    _log(f"saved {len(result.notes_added)} note(s) for '{next_topic.topic}'")

    if result.notes_added:
        try:
            follow_ups = agent.suggest_follow_up_topics(next_topic.topic, result.notes_added)
        except OllamaError as exc:
            _log(f"error suggesting follow-ups: {exc}")
            follow_ups = []
        for candidate in follow_ups:
            if queue.add(candidate):
                _log(f"queued follow-up topic: {candidate}")
    return True


def replenish(agent: Agent, queue: TopicQueue) -> int:
    """Refill a drained queue with self-proposed topics. Returns how many were added."""
    try:
        candidates = agent.propose_new_topics()
    except OllamaError as exc:
        _log(f"error proposing new topics: {exc}")
        return 0

    added = 0
    for candidate in candidates:
        if queue.add(candidate):
            _log(f"self-proposed new topic: {candidate}")
            added += 1
    return added


def seed_queue(queue: TopicQueue, cli_topics: List[str]) -> None:
    """Seed from the command line, falling back to defaults on a cold start."""
    for topic in cli_topics:
        if queue.add(topic):
            _log(f"seeded topic: {topic}")

    # Only fall back to defaults on a genuinely cold start, so restarting the
    # daemon doesn't keep re-adding topics you've already worked through.
    if cli_topics or queue.pending_count() > 0:
        return
    for topic in config.SEED_TOPICS:
        if queue.add(topic):
            _log(f"seeded default topic: {topic}")


def main() -> None:
    memory = Memory()
    agent = Agent(memory=memory)
    queue = TopicQueue()

    seed_queue(queue, sys.argv[1:])

    _log(
        f"starting autonomous research loop "
        f"(interval={config.CURIOSITY_INTERVAL_SECONDS}s, "
        f"max_queue={config.CURIOSITY_MAX_QUEUE_SIZE}). Ctrl+C to stop."
    )

    if queue.pending_count() == 0:
        _log(
            "queue is empty -- add topics with 'python -m llm_agent.daemon <topic> "
            "[topic...]' or /curious in the chat REPL. Polling for new ones."
        )

    # Only try to self-replenish once per drain, otherwise every idle poll
    # would fire off another LLM call.
    replenished_this_drain = False
    try:
        while True:
            processed = run_once(agent, queue)
            if processed:
                replenished_this_drain = False
                time.sleep(config.CURIOSITY_INTERVAL_SECONDS)
                continue

            if not replenished_this_drain:
                replenished_this_drain = True
                if replenish(agent, queue) > 0:
                    continue  # work to do now, don't sleep

            # Nothing to pace, so poll faster and pick up /curious additions
            # from the CLI without waiting a full research interval.
            time.sleep(config.CURIOSITY_IDLE_POLL_SECONDS)
    except KeyboardInterrupt:
        _log("stopping.")
    finally:
        memory.close()
        queue.close()


if __name__ == "__main__":
    main()
