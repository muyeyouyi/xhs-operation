from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..models import TaskInput
from .common import NODE_RETRY_POLICY, ContentState, cancel, logged_node, wait


def build_writing_graph(llm, *, checkpointer=None, metrics=None):
    """输入 brief、topics/topic_index，输出人工审核通过的文章。"""
    def write_article(state):
        article = llm.article(
            TaskInput(**state["brief"]), state["topics"][state["topic_index"]],
            state.get("article"), state.get("decision", {}).get("feedback", ""),
        )
        return article_update(state, article)

    def edit_article(state):
        return article_update(state, state["decision"]["article"])

    def review_article(state) -> Command[Literal["write_article", "edit_article", "cancel", "__end__"]]:
        d = wait(state)
        return Command(update={"decision": d.model_dump()}, goto={
            "approve": END, "revise": "write_article", "edit": "edit_article", "cancel": "cancel",
        }[d.action])

    builder = StateGraph(ContentState)
    for node in [write_article, edit_article, review_article, cancel]:
        builder.add_node(node.__name__, logged_node(node, "写作", metrics), retry_policy=NODE_RETRY_POLICY)
    builder.add_edge(START, "write_article")
    builder.add_edge("write_article", "review_article")
    builder.add_edge("edit_article", "review_article")
    builder.add_edge("cancel", END)
    return builder.compile(checkpointer=checkpointer)


def article_update(state, article):
    return {"article": article, "article_revision": state.get("article_revision", 0) + 1,
            "package": None, "package_revision": 0, "assets": [], "render_error": None, "bundle": None,
            "stage": "article"}
