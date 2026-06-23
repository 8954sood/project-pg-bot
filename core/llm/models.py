from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from core.llm.images import LLMImageInput


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Message:
    author_id: str
    author_name: str
    content: str
    timestamp: datetime = field(default_factory=utc_now)
    images: list[LLMImageInput] = field(default_factory=list)

    @property
    def display_content(self) -> str:
        if self.content:
            return self.content
        if self.images:
            return f"[이미지 첨부 {len(self.images)}장]"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            author_id=str(data["author_id"]),
            author_name=str(data["author_name"]),
            content=str(data["content"]),
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
        )


@dataclass(slots=True)
class BufferedConversation:
    messages: list[Message]
    started_at: datetime
    closed_at: datetime
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def participants(self) -> set[str]:
        return {message.author_id for message in self.messages}

    @property
    def text(self) -> str:
        return "\n".join(f"{message.author_name}: {message.display_content}" for message in self.messages)

    @property
    def images(self) -> list[LLMImageInput]:
        return [image for message in self.messages for image in message.images]


@dataclass(slots=True)
class ServerStyleProfile:
    summary: str = (
        "기본적으로 한국어 반말 위주로 짧고 자연스럽게 답한다. "
        "사용자가 말투 변경을 요청하면 그 스타일을 우선 반영한다. "
        "다만 혐오, 개인정보 노출, 직접적인 괴롭힘 표현은 따라 하지 않는다."
    )
    phrases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UserMemory:
    user_id: str
    user_name: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ServerMemory:
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecentLogEntry:
    role: Literal["user", "assistant"]
    content: str
    id: int | None = None
    author_id: str | None = None
    author_name: str | None = None
    images: list[LLMImageInput] = field(default_factory=list)
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "id": self.id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "images": [
                {
                    "media_type": image.media_type,
                    "data_base64": image.data_base64,
                    "original_bytes": image.original_bytes,
                    "processed_bytes": image.processed_bytes,
                    "filename": image.filename,
                }
                for image in self.images
            ],
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecentLogEntry":
        return cls(
            role=data["role"],
            content=str(data["content"]),
            id=data.get("id"),
            author_id=data.get("author_id"),
            author_name=data.get("author_name"),
            images=[
                LLMImageInput(
                    media_type=str(image["media_type"]),
                    data_base64=str(image["data_base64"]),
                    original_bytes=int(image["original_bytes"]),
                    processed_bytes=int(image["processed_bytes"]),
                    filename=str(image.get("filename", "")),
                )
                for image in data.get("images", [])
                if isinstance(image, dict)
            ],
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
        )


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class MemoryState:
    server_memory: ServerMemory = field(default_factory=ServerMemory)
    server_style: ServerStyleProfile = field(default_factory=ServerStyleProfile)
    active_style_directive: str = ""
    user_memories: dict[str, UserMemory] = field(default_factory=dict)
    relationship_notes: list[str] = field(default_factory=list)
    recent_logs: list[RecentLogEntry] = field(default_factory=list)
    recent_summary: str = ""


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    name: str
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResponseResult:
    should_respond: bool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    prompt: str = ""
    prompt_messages: list[ChatMessage] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class LLMInputMessage:
    guild_id: str
    channel_id: str
    user_id: str
    author_name: str
    content: str
    is_admin: bool = field(default=False, kw_only=True)
    images: list[LLMImageInput] = field(default_factory=list, kw_only=True)


@dataclass(slots=True)
class LLMBufferedMessage(LLMInputMessage):
    created_at: str


@dataclass(slots=True)
class LLMResponseResult:
    ok: bool
    message: str
    response_text: str = ""
