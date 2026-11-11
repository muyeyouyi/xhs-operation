from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..models import TaskInput
from .common import NODE_RETRY_POLICY, ContentState, cancel, logged_node, wait


def build_topics_graph(llm, *, checkpointer=None, metrics=None):
    """输入 brief，输出 topics/topic_index；选择确认后结束子图。"""
    def propose_topics(state):
        topics = llm.topics(TaskInput(**state["brief"]), state.get("decision", {}).get("feedback", ""))
        return {"topics": topics, "stage": "topic"}

    def select_topic(state) -> Command[Literal["propose_topics", "cancel", "__end__"]]:
        d = wait(state)
        update = {"decision": d.model_dump()}
        if d.action == "select":
            update["topic_index"] = d.topic_index
        return Command(update=update, goto={
            "select": END, "regenerate": "propose_topics", "cancel": "cancel",
        }[d.action])

    builder = StateGraph(ContentState)
    for node in [propose_topics, select_topic, cancel]:
        builder.add_node(node.__name__, logged_node(node, "选题", metrics), retry_policy=NODE_RETRY_POLICY)
    builder.add_edge(START, "propose_topics")
    builder.add_edge("propose_topics", "select_topic")
    builder.add_edge("cancel", END)
    return builder.compile(checkpointer=checkpointer)
