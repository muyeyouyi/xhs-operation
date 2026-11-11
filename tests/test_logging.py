import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from media_ops.models import Decision, TaskInput
from media_ops.worker import run_job
from media_ops.workflow import build_graph, current_snapshot
from test_workflow import FakeLLM, FakeRenderer


def start(graph, thread_id="thread-1", job_id=10):
    initial = {"task_id": thread_id, "brief": TaskInput(direction="不应写入日志的完整方向").model_dump()}
    job = {"id": job_id, "task_id": thread_id, "kind": "start"}
    return run_job(graph, job, initial), initial


def resume(graph, initial, snapshot, action, job_id=11, **extra):
    decision = Decision(interrupt_id=snapshot.interrupts[0].id, action=action, **extra)
    return run_job(graph, {"id": job_id, "task_id": initial["task_id"], "kind": "resume",
                           "payload": decision.model_dump()}, initial)


def test_node_timing_and_pause_resume_exclude_human_wait(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="media_ops")
    graph = build_graph(FakeLLM(), FakeRenderer(), InMemorySaver())
    clock = iter([10, 11.25, 12, 12.01])
    monkeypatch.setattr("media_ops.graphs.common.perf_counter", lambda: next(clock))
    snapshot, initial = start(graph)
    assert "流程=thread-1 执行=10 子图=选题 节点=生成候选选题 开始执行" in caplog.text
    assert "节点=生成候选选题 执行完成 耗时=1.250秒" in caplog.text
    assert "节点=选择选题 等待人工操作 耗时=0.010秒" in caplog.text
    assert "执行失败" not in caplog.text
    assert "不应写入日志的完整方向" not in caplog.text

    caplog.clear()
    # 模拟很久之后用户选择选题；只计恢复后本次执行的耗时。
    clock = iter([100, 100.01, 101, 103, 104, 104.01])
    snapshot = resume(graph, initial, snapshot, "select", topic_index=0)
    assert snapshot.values["stage"] == "article"
    assert "执行=11 恢复流程 操作=选择选题" in caplog.text
    assert "执行=11 子图=选题 节点=选择选题 执行完成 耗时=0.010秒" in caplog.text
    assert "节点=生成或修改文章 执行完成 耗时=2.000秒" in caplog.text
    assert "节点=审核文章 等待人工操作" in caplog.text
    assert "执行=10" not in caplog.text


def test_node_failure_is_logged_and_exception_is_preserved(caplog):
    caplog.set_level(logging.INFO, logger="media_ops")
    renderer = FakeRenderer()
    graph = build_graph(FakeLLM(), renderer, InMemorySaver())
    snapshot, initial = start(graph)
    snapshot = resume(graph, initial, snapshot, "select", topic_index=0)
    renderer.failures_remaining = 6
    caplog.clear()
    with pytest.raises(RuntimeError, match="渲染暂时失败"):
        resume(graph, initial, snapshot, "approve", job_id=12)
    assert "流程=thread-1 执行=12 子图=配图 节点=渲染图片 执行失败 耗时=" in caplog.text
    assert "错误类型=RuntimeError" in caplog.text
    assert "节点=渲染图片 执行完成" not in caplog.text
    assert "节点=审核成品 开始执行" not in caplog.text


def test_thread_ids_remain_distinct_and_command_routes_still_visible(caplog):
    caplog.set_level(logging.INFO, logger="media_ops")
    graph = build_graph(FakeLLM(), FakeRenderer(), InMemorySaver())
    start(graph, "thread-a", 20)
    start(graph, "thread-b", 21)
    assert "流程=thread-a 执行=20 子图=选题 节点=生成候选选题" in caplog.text
    assert "流程=thread-b 执行=21 子图=选题 节点=生成候选选题" in caplog.text
    assert "流程=thread-a 执行=21" not in caplog.text
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("topics", "writing") in edges
    assert ("writing", "images") in edges
