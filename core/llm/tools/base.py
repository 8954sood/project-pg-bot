from dataclasses import dataclass
from typing import Any, ClassVar

from core.llm.models import LLMBufferedMessage

SAVE_SCOPE_DESCRIPTION = (
    "user=해당 발화자 개인 저장, server=서버/채널 전역 저장(관리자만). 명시가 없으면 user."
)


@dataclass(slots=True)
class ToolContext:
    guild_id: str
    channel_id: str
    actor: LLMBufferedMessage


# Module-level registry populated by the @register_tool decorator.
_TOOLS: list[type["LLMTool"]] = []


def register_tool(cls: type["LLMTool"]) -> type["LLMTool"]:
    """Register a tool class so LLMToolRegistry auto-discovers it."""
    if cls.name:
        _TOOLS.append(cls)
    return cls


class LLMTool:
    """Base class for a single function-calling tool.

    Subclasses declare ``name``, ``description`` and ``parameters`` (a JSON
    schema dict) as class attributes and implement ``run``. Decorating a
    subclass with :func:`register_tool` makes it available to
    :class:`LLMToolRegistry` without any extra wiring.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {}

    def to_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def run(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        raise NotImplementedError

    @staticmethod
    def parse_scope(arguments: dict[str, Any]) -> str:
        scope = str(arguments.get("scope", "user") or "user").lower()
        return scope if scope in {"user", "server"} else "user"

    @staticmethod
    def parse_note(arguments: dict[str, Any]) -> str:
        return str(arguments.get("note", "") or "").strip()


class LLMToolRegistry:
    """Builds function-calling definitions and dispatches tool calls.

    By default it auto-collects every tool registered via ``@register_tool``.
    Pass an explicit ``tools`` list to override (useful for tests).
    """

    def __init__(self, tools: list[type[LLMTool]] | None = None):
        classes = tools if tools is not None else _TOOLS
        instances = [cls() for cls in classes]
        self.tools: dict[str, LLMTool] = {tool.name: tool for tool in instances}

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [tool.to_definition() for tool in self.tools.values()]

    async def dispatch(self, name: str, arguments: dict[str, Any], *, ctx: ToolContext) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"알 수 없는 툴: {name}"
        return await tool.run(arguments, ctx)