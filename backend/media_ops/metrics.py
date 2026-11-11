from contextvars import ContextVar


# 节点闭包注入 Store，避免把数据库对象放入可持久化的图配置。
current_node_run = ContextVar("current_node_run", default=None)


def record_model(model: str, usage: dict | None = None):
    current = current_node_run.get()
    if current is None:
        return
    store, run_id = current
    if usage is None:
        store.set_node_model(run_id, model)
    else:
        store.set_node_usage(run_id, model, usage)


def record_output(text: str):
    current = current_node_run.get()
    if current is not None and text:
        store, run_id = current
        store.append_node_output(run_id, text)
