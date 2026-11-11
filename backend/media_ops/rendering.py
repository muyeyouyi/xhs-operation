import asyncio
import logging
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class CardOverflow(ValueError):
    pass


class Renderer:
    def __init__(self, data_dir: Path, concurrency: int = 3):
        self.root = data_dir.resolve()
        self.concurrency = concurrency
        self.markdown = MarkdownIt("commonmark", {"html": False})
        self.template = Environment(
            loader=FileSystemLoader(Path(__file__).parent / "templates"),
            autoescape=select_autoescape(["html"]),
        ).get_template("card.html")

    def render(self, task_id: str, article_revision: int, package_revision: int, package: dict):
        relative = Path(task_id) / f"a{article_revision}-p{package_revision}"
        directory = self.root / relative
        directory.mkdir(parents=True, exist_ok=True)
        return asyncio.run(self._render(task_id, relative, package["cards"]))

    async def _render(self, task_id: str, relative: Path, cards: list[dict]):
        semaphore = asyncio.Semaphore(self.concurrency)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                context = await browser.new_context(
                    viewport={"width": 1080, "height": 1440}, device_scale_factor=1,
                )

                async def render_card(index, card):
                    async with semaphore:
                        return await self._render_card(context, task_id, relative, index, len(cards), card)

                tasks = [asyncio.create_task(render_card(index, card)) for index, card in enumerate(cards, 1)]
                try:
                    # gather 按输入顺序返回，图片完成先后不影响审核、导出顺序。
                    return await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                await browser.close()

    async def _render_card(self, context, task_id, relative, index, total, card):
        started = perf_counter()
        logger.info("流程=%s 图片=%s/%s 开始渲染", task_id, index, total)
        page = await context.new_page()
        try:
            html = self.template.render(
                title=card["title"], body=self.markdown.render(card["body"]), index=index, total=total,
            )
            await page.set_content(html, wait_until="load")
            await page.evaluate("document.fonts.ready")
            overflow = await page.locator(".content").evaluate("el => el.scrollHeight > el.clientHeight")
            if overflow:
                raise CardOverflow(f"第 {index} 张卡片内容过长，请填写修改意见，让 AI 精简后重新生成")
            filename = f"{index:02d}.png"
            await page.screenshot(path=str(self.root / relative / filename))
            logger.info("流程=%s 图片=%s/%s 渲染完成 耗时=%.3f秒", task_id, index, total, perf_counter() - started)
            return (relative / filename).as_posix()
        finally:
            await page.close()

    def export(self, state: dict):
        relative = Path(state["task_id"]) / f"a{state['article_revision']}-p{state['package_revision']}" / "素材包.zip"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        article = state["article"]
        package = state["package"]
        markdown = f"# {article['title']}\n\n{article['body']}\n"
        html = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><body>" + self.markdown.render(markdown) + "</body></html>"
        with ZipFile(destination, "w") as archive:
            archive.writestr("微信公众号/文章.md", markdown)
            archive.writestr("微信公众号/文章.html", html)
            archive.writestr("小红书/发布文案.txt", package["title"] + "\n\n" + package["caption"])
            for asset in state["assets"]:
                archive.write(self.root / asset, "小红书/" + Path(asset).name)
        return relative.as_posix()
