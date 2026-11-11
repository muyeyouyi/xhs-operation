import logging
from functools import wraps
from time import perf_counter
from typing import TypedDict

from langgraph.config import get_config
from langgraph.errors import GraphInterrupt
from langgraph.runtime import get_runtime
from langgraph.types import RetryPolicy, StateSnapshot, interrupt

from ..models import Decision, validate_decision
from ..metrics import current_node_run

logger = logging.getLogger(__name__)

NODE_RETRY_POLICY = RetryPolicy(
    initial_interval=1,
    backoff_factor=2,
    max_interval=16,
    max_attempts=6,  # 首次执行 + 5 次重试
    jitter=False,
    retry_on=Exception,
)

NODE_NAMES = {
    "topics": "选题子图", "writing": "写作子图", "images": "配图子图",
    "propose_topics": "生成候选选题", "select_topic": "选择选题",
    "write_article": "生成或修改文章", "edit_article": "保存人工编辑文章",
    "review_article": "审核文章", "make_package": "提炼图片内容和小红书文案",
    "render_package": "渲染图片", "review_package": "审核成品",
    "export": "导出素材包", "cancel": "取消内容任务",
}


class ContentState(TypedDict, total=False):
    task_id: str
    brief: dict
    stage: str
    topics: list[dict]
    topic_index: int
    article: dict | None
    article_revision: int
    package: dict | None
    package_revision: int
    assets: list[str]
    render_error: str | None
    bundle: str | None
    decision: dict


def logged_node(node, subgraph, metrics=None):
    """记录子图及节点单次耗时，不包含人工等待。"""
    @wraps(node)
    def wrapped(state):
        config = get_config()["configurable"]
        attempt = get_runtime().execution_info.node_attempt
        context = (f"流程={config['thread_id']} 执行={config.get('job_id', '-')} "
                   f"子图={subgraph} 节点={NODE_NAMES[node.__name__]}")
        run_id = metrics.start_node(config["thread_id"], config["job_id"], subgraph,
                                    node.__name__, NODE_NAMES[node.__name__]) if metrics else None
        token = current_node_run.set((metrics, run_id) if metrics else None)
        started = perf_counter()
        status = "interrupted"
        error_type = None
        logger.info("%s 开始执行", context)
        if attempt > 1:
            logger.info("%s 正在进行第%d/5次重试", context, attempt - 1)
        try:
            result = node(state)
            status = "completed"
            return result
        except GraphInterrupt:
            status = "waiting"
            raise
        except Exception as exc:
            status, error_type = "failed", type(exc).__name__
            raise
        finally:
            elapsed = perf_counter() - started
            current_node_run.reset(token)
            if metrics:
                metrics.finish_node(run_id, status, elapsed * 1000)
            if status == "failed":
                logger.error("%s 执行失败 耗时=%.3f秒 错误类型=%s", context, elapsed, error_type)
                if attempt < NODE_RETRY_POLICY.max_attempts:
                    logger.warning("%s 将在%d秒后进行第%d/5次重试", context, 2 ** (attempt - 1), attempt)
                else:
                    logger.error("%s 已重试5次，停止本节点执行", context)
            else:
                label = {"waiting": "等待人工操作", "completed": "执行完成", "interrupted": "执行中断"}[status]
                logger.info("%s %s 耗时=%.3f秒", context, label, elapsed)
    return wrapped


def wait(state):
    decision = Decision.model_validate(interrupt({"stage": state["stage"]}))
    validate_decision(state, decision)
    return decision


def cancel(state):
    return {"stage": "cancelled"}


def active_snapshot(snapshot):
    """当前编排串行执行，沿正在执行的子图读取最深一层检查点。"""
    for task in snapshot.tasks:
        if isinstance(task.state, StateSnapshot):
            return active_snapshot(task.state)
    return snapshot
