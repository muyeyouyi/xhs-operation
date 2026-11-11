from langgraph.graph import END, START, StateGraph

from .graphs import ContentState, build_images_graph, build_topics_graph, build_writing_graph
from .graphs.common import NODE_RETRY_POLICY, active_snapshot, logged_node


def build_graph(llm, renderer, checkpointer, *, metrics=None):
    """主图只组合子图和导出，不处理子图内部审核循环。"""
    def export(state):
        return {"bundle": renderer.export(state), "stage": "completed"}

    builder = StateGraph(ContentState)
    builder.add_node("topics", build_topics_graph(llm, metrics=metrics))
    builder.add_node("writing", build_writing_graph(llm, metrics=metrics))
    builder.add_node("images", build_images_graph(llm, renderer, metrics=metrics))
    builder.add_node("export", logged_node(export, "主流程", metrics), retry_policy=NODE_RETRY_POLICY)
    builder.add_edge(START, "topics")
    builder.add_conditional_edges("topics", lambda s: END if s["stage"] == "cancelled" else "writing", ["writing", END])
    builder.add_conditional_edges("writing", lambda s: END if s["stage"] == "cancelled" else "images", ["images", END])
    builder.add_conditional_edges("images", lambda s: END if s["stage"] == "cancelled" else "export", ["export", END])
    builder.add_edge("export", END)
    return builder.compile(checkpointer=checkpointer)


def current_snapshot(graph, config):
    """子图暂停/失败时，将内部最新内容投影给业务层；保留主图执行位置。"""
    snapshot = graph.get_state(config, subgraphs=True)
    values = {**snapshot.values, **active_snapshot(snapshot).values}
    return snapshot._replace(values=values)
