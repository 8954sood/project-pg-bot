from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class LLMConsent:
    guild_id: str
    channel_id: str
    user_id: str
    consented: int
    consent_version: str
    consented_at: Optional[str]
    declined_at: Optional[str]
    updated_at: str


@dataclass(slots=True)
class LLMGlobalMemory:
    id: int
    guild_id: str
    channel_id: Optional[str]
    key: Optional[str]
    content: str
    importance: int
    enabled: int
    created_by: Optional[str]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LLMUserMemory:
    id: int
    guild_id: str
    channel_id: str
    user_id: str
    user_name: str
    key: Optional[str]
    content: str
    importance: int
    enabled: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LLMSpeechStyle:
    guild_id: str
    channel_id: str
    user_id: str
    user_name: str
    notes: str
    phrases: str
    style_summary: str
    updated_at: str


@dataclass(slots=True)
class LLMServerState:
    guild_id: str
    channel_id: str
    server_style_summary: str
    server_style_phrases: str
    active_style_directive: str
    relationship_notes: str
    recent_summary: str
    updated_at: str


@dataclass(slots=True)
class LLMRecentMessage:
    id: int
    guild_id: str
    channel_id: str
    user_id: Optional[str]
    author_name: Optional[str]
    role: str
    content: str
    created_at: str


@dataclass(slots=True)
class LLMMemoryJobState:
    guild_id: str
    channel_id: str
    running: int
    pending_job_id: Optional[str]
    started_at: Optional[str]
    turns_since_last_memory_extraction: int
    memory_extraction_cooldown_turns: int
    last_memory_extraction_had_changes: int
    updated_at: str
