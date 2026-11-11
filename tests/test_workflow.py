from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from media_ops.models import Decision, TaskInput, validate_decision
from media_ops.rendering import CardOverflow
from media_ops.worker import run_job
from media_ops.workflow import build_graph, current_snapshot


class FakeLLM:
    def __init__(self):
        self.topic_calls = []
        self.article_calls = []
        self.package_calls = []

    def topics(self, brief, feedback):
        self.topic_calls.append((brief, feedback))
        return [{"title": f"{brief.direction}-{i}", "angle": "实战", "reason": "适合目标学员"} for i in range(brief.topic_count)]

    def article(self, brief, topic, previous, feedback):
        self.article_calls.append((topic, previous, feedback))
        return {"title": topic["title"], "body": f"正文 {len(self.article_calls)} {feedback}"}

    def package(self, brief, article, previous, feedback):
        self.package_calls.append((article, previous, feedback))
        return {"cards": [{"title": f"知识点 {i}", "body": "示例"} for i in range(brief.card_count)],
                "title": article["title"], "caption": "技术分享 " + feedback}


class FakeRenderer:
    def __init__(self):
        self.calls = 0
        self.fail_once = False
        self.failures_remaining = 0

    def render(self, task_id, article_revision, package_revision, package):
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("渲染暂时失败")
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("渲染暂时失败")
        return [f"{task_id}/a{article_revision}-p{package_revision}/{i}.png" for i in range(len(package["cards"]))]

    def export(self, state):
        return f"{state['task_id']}/素材包.zip"


@pytest.fixture
def flow():
    llm, renderer, saver = FakeLLM(), FakeRenderer(), InMemorySaver()
    graph = build_graph(llm, renderer, saver)
    task_id = str(uuid4())
    initial = {"task_id": task_id, "brief": TaskInput(direction="用 Python 做 PDF 问答助手").model_dump()}
    config = {"configurable": {"thread_id": task_id}}
    job = {"task_id": task_id, "kind": "start", "payload": {}}
    run_job(graph, job, initial)
    return graph, config, llm, renderer, saver, initial


def decide(flow, action, **extra):
    graph, config, _, _, _, initial = flow
    gate = current_snapshot(graph, config).interrupts[0]
    payload = Decision(interrupt_id=gate.id, action=action, **extra).model_dump()
    job = {"task_id": initial["task_id"], "kind": "resume", "payload": payload}
    snapshot = run_job(graph, job, initial)
    return snapshot, job


@pytest.mark.parametrize("count", [3, 4, 5])
def test_direction_and_requested_topic_count(count):
    llm = FakeLLM()
    graph = build_graph(llm, FakeRenderer(), InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid4())}}
    graph.invoke({"task_id": "demo", "brief": TaskInput(direction="AI 训练师的数据标注", topic_count=count).model_dump()}, config)
    state = current_snapshot(graph, config)
    assert state.values["stage"] == "topic"
    assert len(state.values["topics"]) == count
    assert llm.topic_calls[0][0].direction == "AI 训练师的数据标注"
    assert not llm.article_calls


def test_full_review_and_export_flow(flow):
    snapshot, _ = decide(flow, "select", topic_index=1)
    assert snapshot.values["stage"] == "article"
    assert not flow[2].package_calls
    snapshot, _ = decide(flow, "approve")
    assert snapshot.values["stage"] == "package"
    assert len(snapshot.values["assets"]) == 4
    assert not snapshot.values.get("bundle")
    snapshot, _ = decide(flow, "approve")
    assert snapshot.values["stage"] == "completed"
    assert snapshot.values["bundle"].endswith(".zip")
    assert not snapshot.next


def test_regenerate_topics_uses_feedback(flow):
    old = current_snapshot(flow[0], flow[1]).interrupts[0].id
    snapshot, _ = decide(flow, "regenerate", feedback="多讲 API 实践")
    assert snapshot.interrupts[0].id != old
    assert flow[2].topic_calls[-1][1] == "多讲 API 实践"
    assert not flow[2].article_calls


def test_article_revision_and_manual_edit_wait_for_approval(flow):
    decide(flow, "select", topic_index=0)
    snapshot, _ = decide(flow, "revise", feedback="补充环境安装")
    assert snapshot.values["article_revision"] == 2
    assert "补充环境安装" in snapshot.values["article"]["body"]
    assert not flow[2].package_calls
    snapshot, _ = decide(flow, "edit", article={"title": "人工修订标题", "body": "人工正文"})
    assert snapshot.values["article_revision"] == 3
    assert snapshot.values["article"]["body"] == "人工正文"
    assert len(flow[2].article_calls) == 2
    assert snapshot.values["stage"] == "article"
    assert snapshot.values["assets"] == []


def test_package_revision_preserves_approved_article(flow):
    decide(flow, "select", topic_index=0)
    first, _ = decide(flow, "approve")
    snapshot, _ = decide(flow, "revise", feedback="每页更精简")
    assert snapshot.values["article"] == first.values["article"]
    assert snapshot.values["package_revision"] == 2
    assert snapshot.values["stage"] == "package"
    assert not snapshot.values["bundle"]
    assert flow[2].package_calls[-1][2] == "每页更精简"


def test_duplicate_old_decision_does_not_approve_next_gate(flow):
    _, job = decide(flow, "select", topic_index=0)
    run_job(flow[0], job, flow[5])
    snapshot = current_snapshot(flow[0], flow[1])
    assert snapshot.values["stage"] == "article"
    assert len(flow[2].article_calls) == 1
    assert not flow[2].package_calls


def test_rebuild_graph_resumes_same_thread_without_regenerating(flow):
    graph = build_graph(flow[2], flow[3], flow[4])
    resumed = (graph, *flow[1:])
    snapshot, _ = decide(resumed, "select", topic_index=0)
    assert snapshot.values["stage"] == "article"
    assert len(flow[2].topic_calls) == 1


def test_render_failure_retries_from_checkpoint_without_rewriting(flow):
    decide(flow, "select", topic_index=0)
    graph, config, llm, renderer, _, initial = flow
    renderer.failures_remaining = 6
    gate = current_snapshot(graph, config).interrupts[0]
    job = {"task_id": initial["task_id"], "kind": "resume", "payload":
           Decision(interrupt_id=gate.id, action="approve").model_dump()}
    with pytest.raises(RuntimeError, match="渲染暂时失败"):
        run_job(graph, job, initial)
    snapshot = run_job(graph, job, initial)
    assert snapshot.values["stage"] == "package"
    assert len(llm.article_calls) == len(llm.package_calls) == 1
    assert renderer.calls == 7


@pytest.mark.parametrize("stage", ["topic", "article", "package"])
def test_cancel_at_each_review_stage(flow, stage):
    if stage in ("article", "package"):
        decide(flow, "select", topic_index=0)
    if stage == "package":
        decide(flow, "approve")
    snapshot, _ = decide(flow, "cancel")
    assert snapshot.values["stage"] == "cancelled"
    assert not snapshot.next


def test_invalid_decisions_do_not_enter_generation(flow):
    state = current_snapshot(flow[0], flow[1]).values
    with pytest.raises(ValueError, match="当前阶段"):
        validate_decision(state, Decision(interrupt_id="gate", action="approve"))
    with pytest.raises(ValueError, match="请选择"):
        validate_decision(state, Decision(interrupt_id="gate", action="select", topic_index=99))
    with pytest.raises(ValueError, match="修改意见"):
        validate_decision({"stage": "article"}, Decision(interrupt_id="gate", action="revise"))


def test_card_overflow_returns_to_review_and_can_be_revised(flow, monkeypatch):
    decide(flow, "select", topic_index=0)
    original = flow[3].render

    def overflow(*args):
        raise CardOverflow("第 1 张卡片内容过长")

    monkeypatch.setattr(flow[3], "render", overflow)
    snapshot, _ = decide(flow, "approve")
    assert snapshot.values["stage"] == "package"
    assert snapshot.values["render_error"]
    with pytest.raises(ValueError, match="先精简"):
        validate_decision(snapshot.values, Decision(interrupt_id=snapshot.interrupts[0].id, action="approve"))
    monkeypatch.setattr(flow[3], "render", original)
    snapshot, _ = decide(flow, "revise", feedback="减少文字")
    assert snapshot.values["render_error"] is None
    assert snapshot.values["assets"]
