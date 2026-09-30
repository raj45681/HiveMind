from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    files: list[str] = Field(default_factory=list, max_length=20)
    max_seconds: int = Field(default=900, ge=30, le=3600)


class ProcedureUse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=250)
    revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: str = Field(min_length=1, max_length=800)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["done", "blocked", "failed"]
    summary: str = Field(min_length=1, max_length=1500)
    artifacts: list[str] = Field(default_factory=list, max_length=20)
    verification: list[str] = Field(default_factory=list, max_length=10)
    unresolved: list[str] = Field(default_factory=list, max_length=10)
    used_procedures: list[ProcedureUse] = Field(default_factory=list, max_length=5)


class GoalNode(TaskSpec):
    key: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')


class GoalSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,59}$')
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4000)
    project: str = Field(default='hivemind', pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    tasks: list[GoalNode] = Field(default_factory=list, max_length=20)
    agent: Agent = 'codex'
    max_tasks: int = Field(default=10, ge=1, le=100)
    max_seconds: int = Field(default=1800, ge=30, le=7200)
    max_parallel: int = Field(default=1, ge=1, le=4)

    @model_validator(mode='after')
    def valid_graph(self):
        keys = [node.key for node in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError('Goal node keys must be unique')
        graph = {node.key: node.depends_on for node in self.tasks}
        visiting, done = set(), set()
        def walk(key):
            if key not in graph:
                raise ValueError('Unknown goal dependency: ' + key)
            if key in visiting:
                raise ValueError('Goal dependency graph contains a cycle')
            if key in done:
                return
            visiting.add(key)
            for dependency in graph[key]:
                walk(dependency)
            visiting.remove(key)
            done.add(key)
        for key in graph:
            walk(key)
        return self


ROUTES = {"analysis": "codex", "implementation": "codex", "research": "grok",
          "frontend": "antigravity", "review": "grok"}
