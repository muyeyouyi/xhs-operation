from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from media_ops.metrics import record_model
from media_ops.worker import run_job
from media_ops.workflow import build_graph, current_snapshot
from test_postgres import db_flow, process
from test_workflow import FakeLLM, FakeRenderer


def test_metrics_visible_while_node_is_running(db_flow):
    store, _, client, _ = db_flow
    entered, release = Event(), Event()

    class BlockingLLM(FakeLLM):
        def topics(self, brief, feedback):
            record_model("test-model")
            entered.set()
            assert release.wait(10), "测试未释放模型调用"
            record_model("test-model", {"prompt_tokens": 136, "completion_tokens": 501, "total_tokens": 637})
            return super().topics(brief, feedback)

    task = client.post("/api/v1/tasks", json={"direction": "Python 模型调用实战"}).json()
    job = store.claim()
    with PostgresSaver.from_conn_string(store.url) as saver, ThreadPoolExecutor(1) as executor:
        graph = build_graph(BlockingLLM(), FakeRenderer(), saver, metrics=store)
        future = executor.submit(run_job, graph, job, task["state"])
        try:
            assert entered.wait(10)
            rows = client.get(f"/api/v1/tasks/{task['id']}/metrics").json()["nodes"]
            assert len(rows) == 1
            assert rows[0]["status"] == "running"
            assert rows[0]["node_name"] == "生成候选选题"
            assert rows[0]["model"] == "test-model"
            assert rows[0]["total_tokens"] is None
        finally:
            release.set()
        store.finish(job, future.result(timeout=10))
    rows = store.node_runs(task["id"])
    assert [row["status"] for row in rows] == ["completed", "waiting"]
    assert rows[0]["total_tokens"] == 637
    assert rows[0]["input_tokens"] == 136 and rows[0]["output_tokens"] == 501
    assert rows[0]["duration_ms"] > 0
    assert rows[1]["model"] is None
    assert rows[1]["duration_ms"] < 1000


def test_failed_attempt_preserves_usage_and_retry_adds_new_run(db_flow):
    store, _, client, _ = db_flow

    class InvalidLLM(FakeLLM):
        def topics(self, brief, feedback):
            record_model("test-model", {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
            raise ValueError("模型输出格式错误")

    task = client.post("/api/v1/tasks", json={"direction": "AI 应用开发"}).json()
    job = store.claim()
    with PostgresSaver.from_conn_string(store.url) as saver:
        graph = build_graph(InvalidLLM(), FakeRenderer(), saver, metrics=store)
        with pytest.raises(ValueError, match="输出格式"):
            run_job(graph, job, task["state"])
        store.finish(job, current_snapshot(graph, {"configurable": {"thread_id": task["id"]}}), "输出格式错误")
    failed = store.node_runs(task["id"])[0]
    assert failed["status"] == "failed" and failed["total_tokens"] == 30
    assert client.post(f"/api/v1/tasks/{task['id']}/retry").status_code == 202
    with PostgresSaver.from_conn_string(store.url) as saver:
        graph = build_graph(FakeLLM(), FakeRenderer(), saver, metrics=store)
        process(store, graph)
    rows = store.node_runs(task["id"])
    assert [row["status"] for row in rows] == ["failed"] * 6 + ["completed", "waiting"]
    assert rows[0]["job_id"] == rows[5]["job_id"]
    assert rows[0]["job_id"] != rows[6]["job_id"]
    assert sum(row["total_tokens"] or 0 for row in rows) == 180


def test_missing_usage_stays_unknown_and_metrics_are_task_scoped(db_flow):
    store, _, client, _ = db_flow

    class NoUsageLLM(FakeLLM):
        def topics(self, brief, feedback):
            record_model("no-usage-model")
            return super().topics(brief, feedback)

    task = client.post("/api/v1/tasks", json={"direction": "Python 技术干货"}).json()
    with PostgresSaver.from_conn_string(store.url) as saver:
        process(store, build_graph(NoUsageLLM(), FakeRenderer(), saver, metrics=store))
    row = store.node_runs(task["id"])[0]
    assert row["model"] == "no-usage-model"
    assert row["total_tokens"] is None and row["input_tokens"] is None
    other = client.post("/api/v1/tasks", json={"direction": "另一个方向"}).json()
    assert client.get(f"/api/v1/tasks/{other['id']}/metrics").json() == {"nodes": []}
    # 不留下排队任务，避免影响后续集成测试。
    with PostgresSaver.from_conn_string(store.url) as saver:
        process(store, build_graph(FakeLLM(), FakeRenderer(), saver, metrics=store))
    assert client.get("/api/v1/tasks/nonexistent/metrics").status_code == 404


def test_worker_recovery_marks_incomplete_metric_interrupted(db_flow):
    store, graph, client, _ = db_flow
    task = client.post("/api/v1/tasks", json={"direction": "Python 智能助手"}).json()
    job = store.claim()
    store.start_node(task["id"], job["id"], "选题", "propose_topics", "生成候选选题")
    store.recover_running_jobs()
    row = store.node_runs(task["id"])[0]
    assert row["status"] == "interrupted" and row["duration_ms"] is None
    process(store, graph)
    assert [row["status"] for row in store.node_runs(task["id"])] == ["interrupted", "completed", "waiting"]
