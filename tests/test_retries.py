from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START

from media_ops.graphs.common import NODE_RETRY_POLICY
from media_ops.models import Decision, TaskInput
from media_ops.worker import run_job
from media_ops.workflow import build_graph
from test_workflow import FakeLLM, FakeRenderer


def initial_state():
    return {"task_id": str(uuid4()), "brief": TaskInput(direction="Python AI 实战").model_dump()}


def start(graph, state):
    return run_job(graph, {"task_id": state["task_id"], "kind": "start"}, state)


def resume(graph, state, snapshot, action, **extra):
    decision = Decision(interrupt_id=snapshot.interrupts[0].id, action=action, **extra)
    return run_job(graph, {"task_id": state["task_id"], "kind": "resume", "payload": decision.model_dump()}, state)


def test_retry_policy_applies_to_each_leaf_but_not_subgraph_containers():
    graph = build_graph(FakeLLM(), FakeRenderer(), InMemorySaver())
    for name, child in graph.get_subgraphs():
        assert not graph.nodes[name].retry_policy
        for node_name, node in child.nodes.items():
            if node_name != START:
                assert node.retry_policy == (NODE_RETRY_POLICY,)
    assert graph.nodes["export"].retry_policy == (NODE_RETRY_POLICY,)


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_five_retries_use_exact_exponential_delays_and_preserve_error(retry_delays, caplog, error_type):
    error = error_type("测试节点持续失败")

    class FailingLLM(FakeLLM):
        attempts = 0

        def topics(self, brief, feedback):
            self.attempts += 1
            raise error

    llm = FailingLLM()
    graph = build_graph(llm, FakeRenderer(), InMemorySaver())
    with pytest.raises(error_type) as caught:
        start(graph, initial_state())
    assert caught.value is error
    assert llm.attempts == 6
    assert retry_delays == [1, 2, 4, 8, 16]
    assert "将在1秒后进行第1/5次重试" in caplog.text
    assert "将在16秒后进行第5/5次重试" in caplog.text
    assert "已重试5次，停止本节点执行" in caplog.text
    assert not llm.article_calls


@pytest.mark.parametrize("target", ["topics", "article", "package", "render", "export"])
def test_node_recovers_without_repeating_upstream_nodes(target, monkeypatch, retry_delays):
    llm, renderer = FakeLLM(), FakeRenderer()
    owner = llm if target in {"topics", "article", "package"} else renderer
    original = getattr(owner, target)
    calls = 0

    def flaky(*args):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("测试临时故障")
        return original(*args)

    monkeypatch.setattr(owner, target, flaky)
    graph = build_graph(llm, renderer, InMemorySaver())
    state = initial_state()
    snapshot = start(graph, state)
    snapshot = resume(graph, state, snapshot, "select", topic_index=0)
    snapshot = resume(graph, state, snapshot, "approve")
    snapshot = resume(graph, state, snapshot, "approve")
    assert snapshot.values["stage"] == "completed"
    assert calls == 3 and retry_delays == [1, 2]
    assert len(llm.topic_calls) == len(llm.article_calls) == len(llm.package_calls) == 1


def test_human_interrupts_and_normal_revisions_do_not_retry(retry_delays):
    llm = FakeLLM()
    graph = build_graph(llm, FakeRenderer(), InMemorySaver())
    state = initial_state()
    snapshot = start(graph, state)
    snapshot = resume(graph, state, snapshot, "regenerate", feedback="换个角度")
    snapshot = resume(graph, state, snapshot, "select", topic_index=0)
    snapshot = resume(graph, state, snapshot, "revise", feedback="增加案例")
    snapshot = resume(graph, state, snapshot, "approve")
    snapshot = resume(graph, state, snapshot, "cancel")
    assert snapshot.values["stage"] == "cancelled"
    assert retry_delays == []
    assert len(llm.topic_calls) == len(llm.article_calls) == 2
