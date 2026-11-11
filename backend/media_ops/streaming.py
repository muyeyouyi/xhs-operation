import asyncio
import json

from starlette.concurrency import run_in_threadpool


async def stream_updates(store, task_id):
    """SSE 推送最新草稿快照；重新连接直接恢复当前内容，无需事件重放表。"""
    previous = object()
    while True:
        draft = await run_in_threadpool(store.latest_node_output, task_id)
        if draft != previous:
            yield f"data: {json.dumps(draft, ensure_ascii=False)}\n\n"
            previous = draft
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(0.25)
