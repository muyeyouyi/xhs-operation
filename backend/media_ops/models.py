from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TaskInput(BaseModel):
    direction: str = Field(min_length=2, max_length=2000)
    audience: str = Field(default="想学习 AI 应用开发的编程初学者", max_length=1000)
    references: str = Field(default="", max_length=20000)
    topic_count: int = Field(default=4, ge=3, le=5)
    card_count: int = Field(default=4, ge=3, le=5)

    @model_validator(mode="after")
    def normalize_direction(self):
        self.direction = self.direction.strip()
        if len(self.direction) < 2:
            raise ValueError("请输入至少两个字符的内容方向")
        return self


class Topic(BaseModel):
    title: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Topics(BaseModel):
    topics: list[Topic] = Field(min_length=3, max_length=5)


class Article(BaseModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


class Card(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=650)


class Package(BaseModel):
    cards: list[Card] = Field(min_length=3, max_length=5)
    title: str = Field(min_length=1)
    caption: str = Field(min_length=1)


class Decision(BaseModel):
    interrupt_id: str
    action: Literal["select", "regenerate", "approve", "revise", "edit", "cancel"]
    topic_index: int | None = None
    feedback: str = Field(default="", max_length=4000)
    article: Article | None = None


ACTIONS = {
    "topic": {"select", "regenerate", "cancel"},
    "article": {"approve", "revise", "edit", "cancel"},
    "package": {"approve", "revise", "cancel"},
}


def validate_decision(state: dict, decision: Decision):
    stage = state.get("stage")
    if decision.action not in ACTIONS.get(stage, set()):
        raise ValueError("当前阶段不支持此操作")
    if decision.action == "select":
        index = decision.topic_index
        if index is None or not 0 <= index < len(state["topics"]):
            raise ValueError("请选择当前列表中的选题")
    if decision.action == "revise" and not decision.feedback.strip():
        raise ValueError("请填写修改意见")
    if decision.action == "edit" and decision.article is None:
        raise ValueError("请提交编辑后的文章")
    if stage == "package" and decision.action == "approve" and state.get("render_error"):
        raise ValueError("图片内容溢出，请先精简并重新生成")
