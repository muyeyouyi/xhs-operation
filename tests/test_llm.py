import json
from types import SimpleNamespace

import httpx
import pytest

from media_ops.config import Settings
from media_ops.llm import LLM
from media_ops.models import TaskInput
from media_ops.metrics import current_node_run


def model_response(request, data):
    if not json.loads(request.content).get("stream"):
        return httpx.Response(200, json=data)
    choice = data["choices"][0]
    content = choice["message"]["content"]
    frames = []
    for start in range(0, len(content), 11):
        frames.append({"model": data.get("model"), "choices": [{
            "delta": {"content": content[start:start + 11]}, "finish_reason": None,
        }]})
    frames.append({"choices": [{"delta": {}, "finish_reason": choice.get("finish_reason", "stop")}]})
    if "usage" in data:
        frames.append({"choices": [], "usage": data["usage"]})
    body = "".join("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n" for frame in frames)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body + "data: [DONE]\n\n")


def test_compatible_api_receives_direction_and_validates_topics(monkeypatch):
    captured = []

    def respond(request):
        payload = json.loads(request.content)
        captured.append(payload)
        context = json.loads(payload["messages"][1]["content"].split("\n", 1)[1])
        brief = context["brief"]
        topics = [{"title": brief["direction"], "angle": str(i), "reason": "适合初学者"} for i in range(3)]
        return model_response(request, {"choices": [{"message": {"content": json.dumps({"topics": topics})}}]})

    original = httpx.Client
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    llm = LLM(Settings(llm_api_key="test-only", llm_model="test-model", llm_base_url="https://model.invalid/v1"))
    topics = llm.topics(TaskInput(direction="AI 训练师的数据清洗", topic_count=3), "增加实践")
    assert len(topics) == 3
    assert topics[0]["title"] == "AI 训练师的数据清洗"
    assert captured[0]["model"] == "test-model"
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert captured[0]["stream"] is True
    assert captured[0]["stream_options"] == {"include_usage": True}
    assert captured[0]["max_tokens"] == 768
    assert "JSON Schema" not in captured[0]["messages"][0]["content"]
    context = json.loads(captured[0]["messages"][1]["content"].split("\n", 1)[1])
    assert set(context["brief"]) == {"direction", "audience"}
    assert "增加实践" in captured[0]["messages"][1]["content"]


def test_missing_model_configuration_does_not_return_demo_data():
    llm = LLM(Settings(llm_api_key="", llm_model=""))
    with pytest.raises(ValueError, match="配置"):
        llm.topics(TaskInput(direction="Python 实战"), "")


@pytest.mark.parametrize("valid_output", [True, False])
def test_provider_usage_recorded_before_output_validation(monkeypatch, valid_output):
    usage = {"prompt_tokens": 136, "completion_tokens": 501, "total_tokens": 637}
    recorded = []
    content = json.dumps({"topics": [{"title": "题目", "angle": "角度", "reason": "理由"}] * 3}) if valid_output else "无效 JSON"
    response = {"model": "actual-returned-model", "usage": usage,
                "choices": [{"message": {"content": content}}]}
    original = httpx.Client
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: model_response(request, response)), **kwargs))
    recorder = SimpleNamespace(
        set_node_model=lambda run, model: recorded.append((run, model)),
        set_node_usage=lambda run, model, data: recorded.append((run, model, data)),
        append_node_output=lambda run, text: None,
    )
    token = current_node_run.set((recorder, 9))
    try:
        llm = LLM(Settings(llm_api_key="test-only", llm_model="requested-model", llm_base_url="https://model.invalid/v1"))
        if valid_output:
            llm.topics(TaskInput(direction="Python 实战", topic_count=3), "")
        else:
            with pytest.raises(ValueError):
                llm.topics(TaskInput(direction="Python 实战", topic_count=3), "")
    finally:
        current_node_run.reset(token)
    assert recorded == [(9, "requested-model"), (9, "actual-returned-model", usage)]


@pytest.mark.parametrize("stage", ["article", "package"])
def test_other_stages_preserve_provider_thinking_default(monkeypatch, stage):
    captured = []
    article = {"title": "AI 应用入门", "body": "从一次模型调用开始"}
    package = {"title": "技术干货", "caption": "实践分享", "cards": [{"title": "步骤", "body": "示例"}] * 4}

    def respond(request):
        captured.append(json.loads(request.content))
        return model_response(request, {"choices": [{"message": {
            "content": json.dumps(article if stage == "article" else package),
        }}]})

    original = httpx.Client
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    llm = LLM(Settings(llm_api_key="test-only", llm_model="test-model", llm_base_url="https://model.invalid/v1"))
    brief = TaskInput(direction="Python 实战")
    if stage == "article":
        llm.article(brief, {"title": "选题"}, None, "")
    else:
        llm.package(brief, article, None, "")
    assert captured[0].get("stream", False) == (stage == "article")
    assert "thinking" not in captured[0]
    assert "max_tokens" not in captured[0]
    assert "JSON Schema" in captured[0]["messages"][0]["content"]


@pytest.mark.parametrize("count", [3, 4, 5])
def test_compact_topics_keep_references_feedback_and_requested_count(monkeypatch, count):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        topics = [{"title": f"实战选题{i}", "angle": "完成一个最小案例", "reason": "适合新手"} for i in range(count)]
        return model_response(request, {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"topics": topics}),
        }}]})

    original = httpx.Client
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    llm = LLM(Settings(llm_api_key="test-only", llm_model="test-model", llm_base_url="https://model.invalid/v1"))
    result = llm.topics(TaskInput(direction="AI 应用开发", references="课程已有 Python 基础", topic_count=count), "增加实战比例")
    assert len(result) == count
    payload = captured[0]
    assert payload["max_tokens"] == count * 256
    instruction, context_json = payload["messages"][1]["content"].split("\n", 1)
    context = json.loads(context_json)
    assert f"{count}个" in instruction
    assert context["brief"]["references"] == "课程已有 Python 基础"
    assert context["feedback"] == "增加实战比例"
    assert "card_count" not in context["brief"]


def test_truncated_topics_are_not_accepted(monkeypatch):
    original = httpx.Client
    content = json.dumps({"topics": [{"title": "选题", "angle": "角度", "reason": "理由"}] * 3})
    response = {"choices": [{"finish_reason": "length", "message": {"content": content}}]}
    monkeypatch.setattr("media_ops.llm.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: model_response(request, response)), **kwargs))
    llm = LLM(Settings(llm_api_key="test-only", llm_model="test-model", llm_base_url="https://model.invalid/v1"))
    with pytest.raises(ValueError, match="长度上限"):
        llm.topics(TaskInput(direction="Python 实战", topic_count=3), "")
