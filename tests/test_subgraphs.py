from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from media_ops.graphs import ContentState, build_images_graph, build_topics_graph, build_writing_graph
from media_ops.models import Decision, TaskInput
from media_ops.worker import run_job
from media_ops.workflow import current_snapshot
from test_workflow import FakeLLM, FakeRenderer


def seed():
    return {
        "task_id": str(uuid4()),
        "brief": TaskInput(direction="Python AI 应用开发").model_dump(),
        "topics": [{"title": "第一个 AI 应用", "angle": "实战", "reason": "适合初学者"}],
        "topic_index": 0,
        "article": {"title": "AI 应用入门", "body": "从一个最小案例开始。"},
        "article_revision": 1,
    }


def approve(graph, state, action="approve", **extra):
    config = {"configurable": {"thread_id": state["task_id"]}}
    gate = current_snapshot(graph, config).interrupts[0]
    decision = Decision(interrupt_id=gate.id, action=action, **extra)
    return run_job(graph, {"task_id": state["task_id"], "kind": "resume", "payload": decision.model_dump()}, state)


@pytest.mark.parametrize("name", ["topics", "writing", "images"])
def test_subgraph_runs_independently(name):
    llm, renderer, saver = FakeLLM(), FakeRenderer(), InMemorySaver()
    state = seed()
    if name == "topics":
        state = {key: state[key] for key in ("task_id", "brief")}
        graph = build_topics_graph(llm, checkpointer=saver)
        action, extra = "select", {"topic_index": 1}
    elif name == "writing":
        state.pop("article")
        graph = build_writing_graph(llm, checkpointer=saver)
        action, extra = "approve", {}
    else:
        state.pop("topics")
        state.pop("topic_index")
        graph = build_images_graph(llm, renderer, checkpointer=saver)
        action, extra = "approve", {}
    config = {"configurable": {"thread_id": state["task_id"]}}
    graph.invoke(state, config)
    assert current_snapshot(graph, config).interrupts
    snapshot = approve(graph, state, action, **extra)
    assert not snapshot.interrupts and not snapshot.next
    assert len(llm.topic_calls) == (name == "topics")
    assert len(llm.article_calls) == (name == "writing")
    assert len(llm.package_calls) == (name == "images")


def test_recompose_topics_and_writing_without_images():
    llm = FakeLLM()
    builder = StateGraph(ContentState)
    builder.add_node("choose", build_topics_graph(llm))
    builder.add_node("write", build_writing_graph(llm))
    builder.add_edge(START, "choose")
    builder.add_edge("choose", "write")
    builder.add_edge("write", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    state = {key: value for key, value in seed().items() if key in ("task_id", "brief")}
    config = {"configurable": {"thread_id": state["task_id"]}}
    graph.invoke(state, config)
    # 主图此刻未收到子图输出，业务投影能读取内部已生成的选题。
    assert "topics" not in graph.get_state(config).values
    assert len(current_snapshot(graph, config).values["topics"]) == 4
    approve(graph, state, "select", topic_index=0)
    snapshot = approve(graph, state)
    assert not snapshot.next
    assert snapshot.values["article"]
    assert not snapshot.values.get("package")
    assert not llm.package_calls
