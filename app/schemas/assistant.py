from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("empty question")
        return value.strip()

    @model_validator(mode="after")
    def bounded_history(self):
        if sum(len(m.content) for m in self.history) > 12000: raise ValueError("history too large")
        for i, message in enumerate(self.history):
            if message.role != ("user" if i % 2 == 0 else "assistant"): raise ValueError("history must contain complete turns")
        if len(self.history) % 2: raise ValueError("history must contain complete turns")
        return self
