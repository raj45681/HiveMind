from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

# Identity is independent of the CLI used to execute a task. Keep it a short,
# portable slug so any MCP-capable harness can share sessions and ownership.
Agent = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4000)
    project: str = Field(default="hivemind", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    kind: Literal["analysis", "implementation", "research", "frontend", "review"] = "analysis"
    agent: Agent | None = None
    machine: str = Field(default="any", max_length=100)
    access: Literal["read", "write"] = "read"
    acceptance: list[str] = Field(min_length=1, max_length=10)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    memory: list[str] = Field(default_factory=list, max_length=5)
    max_seconds: int = Field(default=900, ge=30, le=3600)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["done", "blocked", "failed"]
    summary: str = Field(min_length=1, max_length=1500)
    artifacts: list[str] = Field(default_factory=list, max_length=20)
    verification: list[str] = Field(default_factory=list, max_length=10)
    unresolved: list[str] = Field(default_factory=list, max_length=10)


ROUTES = {"analysis": "codex", "implementation": "codex", "research": "grok",
          "frontend": "antigravity", "review": "grok"}
