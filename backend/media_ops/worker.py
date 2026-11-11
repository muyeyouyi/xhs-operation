import logging
import time
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from .config import Settings
from .db import Store
from .llm import LLM
from .rendering import Renderer
from .graphs.common import NODE_NAMES
from .workflow import build_graph, current_snapshot

logger = logging.getLogger(__name__)

ACTION_NAMES = {
    "select": "选择选题", "regenerate": "重新推荐选题", "approve": "审核通过",
    "revise": "按意见修改", "edit": "人工编辑文章", "cancel": "取消任务",
}
STAGE_NAMES = {
    "topic": "选择选题", "article": "审核文章", "package": "审核成品",
    "completed": "已完成", "cancelled": "已取消",
}


def configure_logging(log_file: Path = Path("logs/worker.log")):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        ],
    )
    # 由节点包装器输出中文重试日志，避免框架每次重试重复打印英文堆栈。
    logging.getLogger("langgraph.pregel._retry").setLevel(logging.WARNING)
    logger.info("后台执行器日志文件：%s", log_file.resolve())


def run_job(graph, job, initial_state):
    config = {"configurable": {"thread_id": job["task_id"], "job_id": job.get("id", "-")}}
    context = f"流程={job['task_id']} 执行={job.get('id', '-')}"
    snapshot = current_snapshot(graph, config)
    if not snapshot.values:
        logger.info("%s 开始内容生产流程", context)
        graph.invoke(initial_state, config)
    elif snapshot.interrupts:
        if job["kind"] == "resume":
            decision = job["payload"]
            # 重投旧审核命令时，不能推进下一个审核点。
            if snapshot.interrupts[0].id == decision["interrupt_id"]:
                logger.info("%s 恢复流程 操作=%s", context, ACTION_NAMES[decision["action"]])
                graph.invoke(Command(resume={decision["interrupt_id"]: decision}), config)
            else:
                logger.info("%s 跳过重复或过期的审核操作", context)
        else:
            logger.info("%s 流程已在等待人工操作，无需重复执行", context)
    elif snapshot.next:
        logger.info("%s 从失败位置继续 节点=%s", context, "、".join(NODE_NAMES[name] for name in snapshot.next))
        graph.invoke(None, config)
    else:
        logger.info("%s 流程已经结束，无需重复执行", context)
    return current_snapshot(graph, config)


def main():
    configure_logging()
    settings = Settings()
    store = Store(settings.database_url)
    # 首期只运行一个 Worker；进程退出后 PostgreSQL 自动释放会话锁。
    with psycopg.connect(settings.database_url, autocommit=True) as lock_conn:
        if not lock_conn.execute("SELECT pg_try_advisory_lock(108001)").fetchone()[0]:
            raise RuntimeError("已有 Worker 在运行，首期只支持单 Worker")
        store.recover_running_jobs()
        with PostgresSaver.from_conn_string(settings.database_url) as saver:
            graph = build_graph(LLM(settings), Renderer(settings.data_dir, settings.render_concurrency), saver, metrics=store)
            logger.info("后台执行器已启动，等待任务")
            while True:
                lock_conn.execute("SELECT 1")
                job = store.claim()
                if job is None:
                    time.sleep(1)
                    continue
                config = {"configurable": {"thread_id": job["task_id"]}}
                context = f"流程={job['task_id']} 执行={job['id']}"
                started = time.perf_counter()
                logger.info("%s 开始处理 类型=%s", context, "创建内容" if job["kind"] == "start" else "审核恢复")
                try:
                    snapshot = run_job(graph, job, store.get(job["task_id"])["state"])
                    store.finish(job, snapshot)
                    status = "等待人工操作" if snapshot.interrupts else "本次处理结束"
                    stage = snapshot.values.get("stage")
                    logger.info("%s %s 阶段=%s 本次耗时=%.3f秒", context, status,
                                STAGE_NAMES.get(stage, stage), time.perf_counter() - started)
                except Exception as exc:
                    # 仅在进程边界统一记录失败，节点不吞错、不返回假内容。
                    logger.exception("%s 本次处理失败 耗时=%.3f秒",
                                     context, time.perf_counter() - started)
                    store.finish(job, current_snapshot(graph, config), str(exc))


if __name__ == "__main__":
    main()
