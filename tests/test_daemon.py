"""Tests for the autonomous research loop's single-cycle behaviour and seeding."""

import pytest

from llm_agent import config, daemon
from llm_agent.agent import LearnResult
from llm_agent.llm_client import OllamaError
from llm_agent.topics import TopicQueue


class FakeAgent:
    def __init__(
        self,
        notes=None,
        follow_ups=None,
        learn_error=None,
        proposals=None,
        reachable=True,
    ):
        self._notes = notes if notes is not None else [("http://a", "a note")]
        self._follow_ups = follow_ups or []
        self._learn_error = learn_error
        self._proposals = proposals or []
        self._reachable = reachable
        self.learned = []
        self.suggested = []
        self.propose_calls = 0

    def propose_new_topics(self, limit=3):
        self.propose_calls += 1
        return list(self._proposals)

    def learn(self, topic):
        self.learned.append(topic)
        if self._learn_error:
            raise self._learn_error
        return LearnResult(
            topic=topic, notes_added=list(self._notes), reachable=self._reachable
        )

    def suggest_follow_up_topics(self, topic, notes):
        self.suggested.append(topic)
        return list(self._follow_ups)


@pytest.fixture
def queue(tmp_path):
    q = TopicQueue(db_path=tmp_path / "test.db")
    yield q
    q.close()


def test_run_once_on_empty_queue_returns_false(queue):
    agent = FakeAgent()
    assert daemon.run_once(agent, queue) is False
    assert agent.learned == []


def test_run_once_researches_next_topic(queue):
    queue.add("cats")
    agent = FakeAgent()

    assert daemon.run_once(agent, queue) is True
    assert agent.learned == ["cats"]
    assert queue.pending_count() == 0


def test_run_once_queues_follow_ups(queue):
    queue.add("cats")
    agent = FakeAgent(follow_ups=["cat nutrition", "cat sleep"])

    daemon.run_once(agent, queue)

    assert [t.topic for t in queue.pending()] == ["cat nutrition", "cat sleep"]


def test_run_once_skips_follow_ups_when_nothing_learned(queue):
    queue.add("cats")
    agent = FakeAgent(notes=[], follow_ups=["should not be queued"])

    daemon.run_once(agent, queue)

    assert agent.suggested == []
    assert queue.pending_count() == 0


def test_run_once_requeues_topic_when_ollama_is_down(queue):
    queue.add("cats")
    agent = FakeAgent(learn_error=OllamaError("ollama is down"))

    assert daemon.run_once(agent, queue) is True
    # The topic isn't the reason this failed, so it must survive for a retry.
    assert [t.topic for t in queue.pending()] == ["cats"]


def test_run_once_requeues_topic_when_web_unreachable(queue):
    """The boot case: daemon starts before the network is up."""
    queue.add("cats")
    agent = FakeAgent(notes=[], reachable=False)

    assert daemon.run_once(agent, queue) is True
    assert [t.topic for t in queue.pending()] == ["cats"]


def test_offline_boot_does_not_destroy_the_queue(queue):
    """Regression: an offline daemon used to silently consume every topic."""
    for topic in ("alpha", "beta", "gamma"):
        queue.add(topic)
    agent = FakeAgent(notes=[], reachable=False)

    for _ in range(3):
        daemon.run_once(agent, queue)

    assert {t.topic for t in queue.pending()} == {"alpha", "beta", "gamma"}


def test_topic_is_abandoned_after_repeated_failures(queue):
    """A topic that always fails must not be retried forever."""
    queue.add("cats")
    agent = FakeAgent(notes=[], reachable=False)

    for _ in range(config.CURIOSITY_MAX_ATTEMPTS + 2):
        daemon.run_once(agent, queue)

    assert queue.pending_count() == 0


def test_successful_research_still_completes_the_topic(queue):
    queue.add("cats")
    daemon.run_once(FakeAgent(), queue)

    assert queue.pending_count() == 0


def test_run_once_processes_topics_in_order(queue):
    queue.add("first")
    queue.add("second")
    agent = FakeAgent()

    daemon.run_once(agent, queue)
    daemon.run_once(agent, queue)

    assert agent.learned == ["first", "second"]


# --- seeding ------------------------------------------------------------


def test_seed_queue_uses_cli_topics(queue):
    daemon.seed_queue(queue, ["alpha", "beta"])
    assert [t.topic for t in queue.pending()] == ["alpha", "beta"]


def test_seed_queue_falls_back_to_defaults_on_cold_start(queue):
    daemon.seed_queue(queue, [])
    assert [t.topic for t in queue.pending()] == config.SEED_TOPICS


def test_seed_queue_skips_defaults_when_cli_topics_given(queue):
    daemon.seed_queue(queue, ["alpha"])
    assert [t.topic for t in queue.pending()] == ["alpha"]


def test_seed_queue_skips_defaults_when_queue_not_empty(queue):
    queue.add("leftover from last run")
    daemon.seed_queue(queue, [])
    assert [t.topic for t in queue.pending()] == ["leftover from last run"]


# --- self-replenishment -------------------------------------------------


def test_replenish_adds_self_proposed_topics(queue):
    agent = FakeAgent(proposals=["quantum computing", "tide pools"])

    added = daemon.replenish(agent, queue)

    assert added == 2
    assert [t.topic for t in queue.pending()] == ["quantum computing", "tide pools"]


def test_replenish_returns_zero_when_nothing_proposed(queue):
    assert daemon.replenish(FakeAgent(proposals=[]), queue) == 0
    assert queue.pending_count() == 0


def test_replenish_skips_already_seen_topics(queue):
    """A topic already researched must not be resurrected."""
    queue.add("cats")
    daemon.run_once(FakeAgent(), queue)  # marks 'cats' done

    added = daemon.replenish(FakeAgent(proposals=["cats", "dogs"]), queue)

    assert added == 1
    assert [t.topic for t in queue.pending()] == ["dogs"]


def test_replenish_survives_llm_error(queue):
    class Boom(FakeAgent):
        def propose_new_topics(self, limit=3):
            raise OllamaError("ollama is down")

    assert daemon.replenish(Boom(), queue) == 0


def test_seed_queue_does_not_requeue_completed_defaults(queue):
    """Restarting the daemon shouldn't redo work already done."""
    daemon.seed_queue(queue, [])
    while daemon.run_once(FakeAgent(), queue):
        pass

    daemon.seed_queue(queue, [])

    assert queue.pending_count() == 0
