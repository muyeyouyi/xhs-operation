import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from media_ops.rendering import Renderer


@pytest.mark.parametrize("fail", [False, True])
def test_parallel_render_preserves_order_and_cleans_up(tmp_path, monkeypatch, fail):
    active = 0
    peak = 0
    finished = []
    cancelled = []
    closed = []

    async def new_context(**kwargs):
        return object()

    async def close():
        assert active == 0
        closed.append(True)

    async def launch():
        return SimpleNamespace(new_context=new_context, close=close)

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    async def render_card(context, task_id, relative, index, total, card):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.005 if index == 2 else 0.03)
            if fail and index == 2:
                raise ValueError("测试渲染失败")
            finished.append(index)
            return (relative / f"{index:02d}.png").as_posix()
        except asyncio.CancelledError:
            cancelled.append(index)
            raise
        finally:
            active -= 1

    renderer = Renderer(tmp_path, concurrency=2)
    monkeypatch.setattr("media_ops.rendering.async_playwright", playwright)
    monkeypatch.setattr(renderer, "_render_card", render_card)
    package = {"cards": [{} for _ in range(5)]}
    if fail:
        with pytest.raises(ValueError, match="测试渲染失败"):
            renderer.render("task", 1, 1, package)
        assert cancelled
    else:
        assert renderer.render("task", 1, 1, package) == [f"task/a1-p1/{i:02d}.png" for i in range(1, 6)]
        assert finished[0] == 2
    assert peak == 2
    assert active == 0
    assert closed == [True]


def test_export_contains_both_channels_and_escapes_html(tmp_path):
    renderer = Renderer(tmp_path)
    asset = tmp_path / "task" / "a1-p1" / "01.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"test-image")
    state = {"task_id": "task", "article_revision": 1, "package_revision": 1,
             "article": {"title": "AI 入门", "body": "<script>alert(1)</script>\n\n```python\nprint('hi')\n```"},
             "package": {"title": "小红书标题", "caption": "小红书文案"},
             "assets": ["task/a1-p1/01.png"]}
    bundle = renderer.export(state)
    with ZipFile(tmp_path / bundle) as archive:
        assert set(archive.namelist()) == {"微信公众号/文章.md", "微信公众号/文章.html", "小红书/发布文案.txt", "小红书/01.png"}
        html = archive.read("微信公众号/文章.html").decode()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "language-python" in html
        assert "小红书文案" in archive.read("小红书/发布文案.txt").decode()


@pytest.mark.skipif(not os.environ.get("RUN_RENDER_TESTS"), reason="设置 RUN_RENDER_TESTS=1 启用 Chromium 渲染测试")
def test_actual_chinese_card_render_and_overflow(tmp_path):
    renderer = Renderer(tmp_path)
    package = {"title": "AI 助手入门", "caption": "技术分享", "cards": [
        {"title": "用 Python 做第一个 AI 助手", "body": "先跑通一次模型调用。\n\n- 准备 Python 环境\n- 配置模型 API\n- 验证输入与输出"},
        {"title": "连接问题与答案", "body": "```python\ndef ask(question: str):\n    return model.invoke(question)\n```\n\n先使用一个清晰的问题验证流程。"},
        {"title": "加入你自己的资料", "body": "1. 读取资料\n2. 提取相关片段\n3. 将片段与问题交给模型\n\n**输出需要人工核对。**"},
    ]}
    assets = renderer.render("render-test", 1, 1, package)
    assert len(assets) == 3
    for asset in assets:
        data = (tmp_path / asset).read_bytes()
        assert data[:8].hex() == "89504e470d0a1a0a"
        assert int.from_bytes(data[16:20], "big") == 1080
        assert int.from_bytes(data[20:24], "big") == 1440
    assert renderer.render("render-test", 1, 1, package) == assets
    package["cards"][0]["body"] = "\n\n".join(["过长的内容"] * 50)
    with pytest.raises(ValueError, match="内容过长"):
        renderer.render("render-test", 1, 2, package)
