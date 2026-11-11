from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..models import TaskInput
from ..rendering import CardOverflow
from .common import NODE_RETRY_POLICY, ContentState, cancel, logged_node, wait


def build_images_graph(llm, renderer, *, checkpointer=None, metrics=None):
    """输入 brief、article/article_revision，输出审核通过的成品及图片。"""
    def make_package(state):
        package = llm.package(
            TaskInput(**state["brief"]), state["article"], state.get("package"),
            state.get("decision", {}).get("feedback", ""),
        )
        return {"package": package, "package_revision": state.get("package_revision", 0) + 1,
                "assets": [], "render_error": None, "bundle": None}

    def render_package(state):
        try:
            assets = renderer.render(
                state["task_id"], state["article_revision"], state["package_revision"], state["package"],
            )
        except CardOverflow as exc:
            return {"assets": [], "render_error": str(exc), "stage": "package"}
        return {"assets": assets, "render_error": None, "stage": "package"}

    def review_package(state) -> Command[Literal["make_package", "cancel", "__end__"]]:
        d = wait(state)
        return Command(update={"decision": d.model_dump()}, goto={
            "approve": END, "revise": "make_package", "cancel": "cancel",
        }[d.action])

    builder = StateGraph(ContentState)
    for node in [make_package, render_package, review_package, cancel]:
        builder.add_node(node.__name__, logged_node(node, "配图", metrics), retry_policy=NODE_RETRY_POLICY)
    builder.add_edge(START, "make_package")
    builder.add_edge("make_package", "render_package")
    builder.add_edge("render_package", "review_package")
    builder.add_edge("cancel", END)
    return builder.compile(checkpointer=checkpointer)
