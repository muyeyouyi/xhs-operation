"""TEST_DATABASE_URL 必须指向独立的测试库；不连接默认业务库。"""
import os

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver

from media_ops.api import create_app
from media_ops.config import Settings
from media_ops.db import Store
from media_ops.models import Decision
from media_ops.worker import run_job
from media_ops.workflow import build_graph, current_snapshot
from test_workflow import FakeLLM, FakeRenderer


@pytest.fixture
def db_flow(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 后运行真实 PostgreSQL 集成测试")
    store = Store(url)
    store.setup()
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        llm = FakeLLM()
        graph = build_graph(llm, FakeRenderer(), saver, metrics=store)
        settings = Settings(database_url=url, data_dir=tmp_path)
        with TestClient(create_app(store, settings)) as client:
            yield store, graph, client, llm


def process(store, graph):
    job = store.claim()
    assert job is not None
    snapshot = run_job(graph, job, store.get(job["task_id"])["state"])
    store.finish(job, snapshot)
    return store.get(job["task_id"])


def test_api_postgres_full_flow_and_stale_decision(db_flow):
    store, graph, client, llm = db_flow
    response = client.post("/api/v1/tasks", json={"direction": "Python AI 应用开发", "topic_count": 3})
    assert response.status_code == 202
    task = process(store, graph)
    assert task["status"] == "waiting"
    assert len(task["state"]["topics"]) == 3
    route = f"/api/v1/tasks/{task['id']}"
    decision = {"interrupt_id": task["interrupt_id"], "action": "select", "topic_index": 0}
    assert client.post(route + "/decisions", json=decision).status_code == 202
    assert client.post(route + "/decisions", json=decision).status_code == 409
    task = process(store, graph)
    assert task["state"]["stage"] == "article"
    assert client.post(route + "/decisions", json=decision).status_code == 409
    for stage in ("package", "completed"):
        response = client.post(route + "/decisions", json={"interrupt_id": task["interrupt_id"], "action": "approve"})
        assert response.status_code == 202
        task = process(store, graph)
        assert task["state"]["stage"] == stage
    assert client.get(route).json()["status"] == "completed"
    assert len(client.get(route + "/history").json()) == 4
    assert len(llm.topic_calls) == len(llm.article_calls) == len(llm.package_calls) == 1


@pytest.mark.parametrize("stage", ["topic", "article", "package"])
def test_postgres_checkpoint_survives_new_connection(db_flow, stage):
    store, graph, client, llm = db_flow
    task = client.post("/api/v1/tasks", json={"direction": "AI 训练师入门"}).json()
    task = process(store, graph)
    if stage in ("article", "package"):
        store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="select", topic_index=0))
        task = process(store, graph)
    if stage == "package":
        store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="approve"))
        task = process(store, graph)
    decision = Decision(interrupt_id=task["interrupt_id"], action="select" if stage == "topic" else "approve", topic_index=0)
    store.decide(task["id"], decision)
    with PostgresSaver.from_conn_string(store.url) as saver:
        restored = build_graph(llm, FakeRenderer(), saver, metrics=store)
        task = process(store, restored)
    assert task["state"]["stage"] == {"topic": "article", "article": "package", "package": "completed"}[stage]
    assert len(llm.topic_calls) == 1


def test_history_preserves_each_revision_inside_writing_subgraph(db_flow):
    store, graph, client, _ = db_flow
    client.post("/api/v1/tasks", json={"direction": "Python 实战文章"})
    task = process(store, graph)
    store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="select", topic_index=0))
    task = process(store, graph)
    for feedback in ("补充案例", "精简解释"):
        store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="revise", feedback=feedback))
        task = process(store, graph)
    history = store.history(task["id"])
    assert len(history) == 4
    assert [item["state"]["article_revision"] for item in history if item["state"]["stage"] == "article"] == [1, 2, 3]


def test_api_rejects_blank_direction_and_invalid_count(db_flow):
    _, _, client, _ = db_flow
    for body in ({"direction": "  "}, {"direction": "Python 教程", "topic_count": 2}):
        assert client.post("/api/v1/tasks", json=body).status_code == 422


def test_failed_job_can_retry_without_repeating_completed_nodes(db_flow):
    store, graph, client, llm = db_flow
    client.post("/api/v1/tasks", json={"direction": "Python 智能助手"})
    task = process(store, graph)
    store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="select", topic_index=0))
    task = process(store, graph)
    store.decide(task["id"], Decision(interrupt_id=task["interrupt_id"], action="approve"))
    job = store.claim()
    renderer = FakeRenderer()
    renderer.failures_remaining = 6
    with PostgresSaver.from_conn_string(store.url) as saver:
        graph = build_graph(llm, renderer, saver, metrics=store)
        with pytest.raises(RuntimeError):
            run_job(graph, job, task["state"])
        store.finish(job, current_snapshot(graph, {"configurable": {"thread_id": task["id"]}}), "渲染暂时失败")
        assert client.post(f"/api/v1/tasks/{task['id']}/retry").status_code == 202
        task = process(store, graph)
    assert task["status"] == "waiting"
    assert task["state"]["stage"] == "package"
    assert len(llm.article_calls) == len(llm.package_calls) == 1
