import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from media_ops.config import Settings
from media_ops.llm import LLM, read_stream
from media_ops.metrics import current_node_run
from media_ops.streaming import stream_updates
from media_ops.worker import run_job
from media_ops.workflow import build_graph
from test_postgres import db_flow, process
from test_workflow import FakeRenderer


def frame(data):
    return ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode()


def delta(text):
    return frame({"choices": [{"delta": {"content": text}, "finish_reason": None}]})


def recorder():
    outputs, usages = [], []
    store = SimpleNamespace(append_node_output=lambda run, text: outputs.append(text),
                            set_node_usage=lambda run, model, usage: usages.append((model, usage)))
    return store, outputs, usages


def test_stream_ignores_reasoning_preserves_escapes_and_final_usage():
    store, outputs, usages = recorder()
    content = json.dumps({"title": "中文标题", "body": '代码：\n```python\nprint("hello")\n```'}, ensure_ascii=False)
    body = frame({"model": "actual-model", "choices": [{"delta": {"reasoning_content": "不展示的思考"}}]})
    body += delta(content[:20]) + delta(content[20:])
    body += frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body += frame({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32}})
    body += b'data: [DONE]\n\n'
    token = current_node_run.set((store, 1))
    try:
        text, reason = read_stream(httpx.Response(200, content=body), "requested-model")
    finally:
        current_node_run.reset(token)
    assert text == content and reason == "stop"
    assert ''.join(outputs) == content
    assert "不展示的思考" not in ''.join(outputs)
    assert usages == [("actual-model", {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32})]


@pytest.mark.parametrize("disconnected", [False, True])
def test_incomplete_stream_is_not_accepted_and_preserves_partial_draft(disconnected):
    store, outputs, _ = recorder()

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield delta('{"title":"半截正文')
            if disconnected:
                raise httpx.ReadError("连接中断")
            yield b'data: [DONE]\n\n'

    token = current_node_run.set((store, 1))
    try:
        with pytest.raises(httpx.ReadError if disconnected else ValueError):
            read_stream(httpx.Response(200, stream=BrokenStream()), "test-model")
    finally:
        current_node_run.reset(token)
    assert ''.join(outputs) == '{"title":"半截正文'


def test_sse_snapshots_support_reconnect_progress_and_retry_reset():
    first = {"id": 1, "output_text": '{"title":"初稿'}
    updated = {"id": 1, "output_text": '{"title":"初稿完成"}'}
    retry = {"id": 2, "output_text": '{"title":"新稿'}
    rows = iter([first, updated, updated, retry])
    store = SimpleNamespace(latest_node_output=lambda task_id: next(rows))

    async def collect():
        stream = stream_updates(store, "task")
        try:
            return [await anext(stream) for _ in range(4)]
        finally:
            await stream.aclose()

    frames = asyncio.run(collect())
    assert json.loads(frames[0][6:]) == first
    assert json.loads(frames[1][6:]) == updated
    assert frames[2].startswith(':')
    assert json.loads(frames[3][6:]) == retry
    assert "初稿" not in frames[3]


def test_real_stream_is_visible_in_database_before_model_finishes(db_flow, monkeypatch):
    store, _, client, _ = db_flow
    entered, release = Event(), Event()
    text = json.dumps({"topics": [{"title": "Python 实战", "angle": "调用模型", "reason": "适合新手"}] * 3}, ensure_ascii=False)
    partial = text[:25]

    class ModelStream(httpx.SyncByteStream):
        def __iter__(self):
            yield delta(partial)
            entered.set()
            assert release.wait(10)
            yield delta(text[25:])
            yield frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield frame({"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 40, "total_tokens": 70}})
            yield b'data: [DONE]\n\n'

    original = httpx.Client
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=ModelStream())), **kwargs))
    tick = iter(range(100))
    monkeypatch.setattr("media_ops.llm.monotonic", lambda: next(tick))
    task = client.post('/api/v1/tasks', json={"direction": "Python 调用模型", "topic_count": 3}).json()
    job = store.claim()
    llm = LLM(Settings(llm_api_key='test-only', llm_model='test-model', llm_base_url='https://model.invalid/v1'))
    with PostgresSaver.from_conn_string(store.url) as saver, ThreadPoolExecutor(1) as executor:
        graph = build_graph(llm, FakeRenderer(), saver, metrics=store)
        future = executor.submit(run_job, graph, job, task['state'])
        try:
            assert entered.wait(10)
            draft = store.latest_node_output(task['id'])
            assert draft['status'] == 'running' and draft['output_text'] == partial
            assert 'output_text' not in store.node_runs(task['id'])[0]
        finally:
            release.set()
        store.finish(job, future.result(timeout=10))
    assert store.latest_node_output(task['id'])['output_text'] == text
    assert store.node_runs(task['id'])[0]['total_tokens'] == 70


def test_stream_api_headers_and_snapshot_body(db_flow, monkeypatch):
    store, graph, client, _ = db_flow
    task = client.post('/api/v1/tasks', json={"direction": "Python 实战"}).json()

    async def finite(store, task_id):
        yield 'data: {"id":1,"output_text":"草稿"}\n\n'

    monkeypatch.setattr('media_ops.api.stream_updates', finite)
    response = client.get(f'/api/v1/tasks/{task["id"]}/stream')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['x-accel-buffering'] == 'no'
    assert '草稿' in response.text
    assert client.get('/api/v1/tasks/missing/stream').status_code == 404
    process(store, graph)
