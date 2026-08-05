"""Orchestrates chat (RAG over local memory) and web research.

The model can trigger its own research: if it doesn't know something, it
replies with a `SEARCH: <query>` directive instead of an answer, the agent
runs that search automatically, saves what it learns to memory, and then
asks the model again with the new context. This happens without the user
needing to type a separate command — but every auto-triggered search is
reported back to the caller so it's visible, not silent.

The directive is matched anywhere in the reply rather than as the whole
reply: small models routinely ignore "reply with exactly one line" and
prepend something chatty like "Sure, let me look that up." Anchoring
strictly meant those replies fell through and the raw "SEARCH: ..." text got
shown to the user instead of triggering research.

Research is capped at one automatic round per chat turn so a confused model
can't spiral into repeated web requests.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

from . import config
from . import llm_client
from . import web_search
from .memory import Memory, Note

SEARCH_DIRECTIVE = re.compile(
    r"^\s*SEARCH:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
MAX_SEARCH_QUERY_CHARS = 200

CHAT_SYSTEM_PROMPT = (
    f"You are {config.ASSISTANT_NAME}, a helpful assistant running locally on "
    "the user's Chromebook. Refer to yourself as "
    f"{config.ASSISTANT_NAME} whenever you name yourself. "
    "You have access to a memory of notes you've researched previously, given "
    "below as context — use it when relevant, and say when you're relying on it. "
    "If the context doesn't contain what you need and the question depends on "
    "current, specific, or unfamiliar information you're not confident about, "
    "do not guess. Instead reply with EXACTLY one line in the form:\n"
    "SEARCH: <a short, specific web search query>\n"
    "and nothing else. Otherwise, just answer normally and conversationally."
)


def _extract_search_query(reply: str) -> str:
    """Return the model's requested search query, or '' if it didn't ask for one."""
    match = SEARCH_DIRECTIVE.search(reply or "")
    if not match:
        return ""
    query = match.group(1).strip()
    # A runaway model can emit a whole paragraph after "SEARCH:"; that's not a
    # usable query, so treat it as a normal answer instead.
    if not query or len(query) > MAX_SEARCH_QUERY_CHARS:
        return ""
    return query


def _strip_search_directives(text: str) -> str:
    """Remove any leaked SEARCH: lines so they're never shown to the user."""
    return SEARCH_DIRECTIVE.sub("", text or "").strip()

NOTE_SYSTEM_PROMPT = (
    "You write concise, factual notes for a personal knowledge base. Summarize "
    "only what is actually stated in the given text. Do not speculate or add "
    "outside knowledge. Keep it under 200 words."
)


@dataclass
class ChatResult:
    answer: str
    researched_query: str = ""
    notes_added: int = 0


@dataclass
class LearnResult:
    topic: str
    notes_added: List[Tuple[str, str]] = field(default_factory=list)  # (url, note)
    # False when the web couldn't be reached at all. The daemon uses this to
    # retry the topic later instead of marking it researched.
    reachable: bool = True


class Agent:
    def __init__(self, memory: Memory = None):
        self.memory = memory or Memory()

    def _memory_context(self, query: str) -> str:
        notes = self.memory.search(query, top_k=config.MEMORY_TOP_K)
        if not notes:
            return ""
        lines = ["Relevant notes from memory:"]
        for note in notes:
            lines.append(f"- ({note.topic}, source: {note.source_url or 'n/a'}) {note.content}")
        return "\n".join(lines)

    def learn(self, topic: str) -> LearnResult:
        result = LearnResult(topic=topic)
        try:
            results = web_search.search(topic, num_results=config.SEARCH_RESULTS)
        except web_search.SearchError:
            result.reachable = False
            return result

        for hit in results:
            text = web_search.fetch_text(hit.url)
            if not text:
                continue
            summary = llm_client.chat(
                [
                    {"role": "system", "content": NOTE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Topic: {topic}\n\nPage content:\n{text}",
                    },
                ]
            )
            if not summary:
                continue
            # add() returns None when this exact note is already stored, so a
            # restatement of something we know isn't counted as new learning.
            if self.memory.add(topic=topic, content=summary, source_url=hit.url):
                result.notes_added.append((hit.url, summary))
        return result

    def suggest_follow_up_topics(self, topic: str, notes: List[Tuple[str, str]]) -> List[str]:
        """Ask the model what's worth researching next, based on what it just learned."""
        if not notes:
            return []
        summary_text = "\n".join(note for _url, note in notes)
        reply = llm_client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You help decide what to research next for a personal knowledge "
                        f"base. You just learned about '{topic}'. Suggest up to "
                        f"{config.CURIOSITY_FOLLOW_UPS_PER_TOPIC} closely related topics "
                        "worth researching next. Reply with one topic per line, no "
                        "numbering, no explanation. If nothing is worth following up on, "
                        "reply with NONE."
                    ),
                },
                {"role": "user", "content": summary_text},
            ]
        )
        if not reply or reply.strip().upper() == "NONE":
            return []
        topics = [line.strip("-* \t") for line in reply.splitlines() if line.strip()]
        return topics[: config.CURIOSITY_FOLLOW_UPS_PER_TOPIC]

    def propose_new_topics(self, limit: int = 3) -> List[str]:
        """Invent fresh research directions from what's already in memory.

        Used when the daemon's queue drains. Without this the daemon can stall
        permanently: a research pass that turns up only already-known notes
        produces no follow-ups, and completed topics can never be requeued, so
        the queue empties and nothing ever refills it.
        """
        known = self.memory.recent(limit=10)
        if not known:
            return []

        summary_text = "\n".join(f"- {note.topic}: {note.content}" for note in known)
        reply = llm_client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You maintain a personal knowledge base. Below is a sample of "
                        f"what it already contains. Suggest up to {limit} NEW topics "
                        "worth researching that are related but not already covered. "
                        "Reply with one topic per line, no numbering, no explanation. "
                        "If nothing new seems worthwhile, reply with NONE."
                    ),
                },
                {"role": "user", "content": summary_text},
            ]
        )
        if not reply or reply.strip().upper() == "NONE":
            return []
        topics = [line.strip("-* \t") for line in reply.splitlines() if line.strip()]
        return topics[:limit]

    def chat(self, user_input: str, history: List[Dict[str, str]] = None) -> ChatResult:
        history = history or []
        context = self._memory_context(user_input)

        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        reply = llm_client.chat(messages)
        query = _extract_search_query(reply)
        if not query:
            return ChatResult(answer=reply)

        learned = self.learn(query)

        follow_up_context = self._memory_context(user_input)
        follow_up_messages = [
            {
                "role": "system",
                "content": CHAT_SYSTEM_PROMPT
                + "\n\nYou already researched this once this turn — answer now using "
                "the context below, don't reply with another SEARCH directive.",
            }
        ]
        if follow_up_context:
            follow_up_messages.append({"role": "system", "content": follow_up_context})
        follow_up_messages.extend(history)
        follow_up_messages.append({"role": "user", "content": user_input})

        # The model may still emit another SEARCH: line despite being told not
        # to. Strip it rather than showing the directive to the user.
        final_answer = _strip_search_directives(llm_client.chat(follow_up_messages))
        if not final_answer:
            if learned.notes_added:
                final_answer = (
                    f"I researched \"{query}\" and saved "
                    f"{len(learned.notes_added)} note(s), but couldn't summarize "
                    "an answer. Try asking again, or run /memory to read the notes."
                )
            else:
                final_answer = (
                    f"I tried researching \"{query}\" but couldn't find anything "
                    "usable. You may need to rephrase, or check your connection."
                )

        return ChatResult(
            answer=final_answer,
            researched_query=query,
            notes_added=len(learned.notes_added),
        )
