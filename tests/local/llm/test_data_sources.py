import pytest

from core.local import LocalCore
from core.local import path as path_module


@pytest.mark.asyncio
async def test_llm_state_is_stored_in_sqlite_not_json(tmp_path, monkeypatch):
    monkeypatch.setattr(path_module, "db_path", str(tmp_path / "db.sqlite"))

    await LocalCore.init_tables()
    await LocalCore.llmConsentDataSource.set("1", "2", "3", "v1", True)
    await LocalCore.llmGlobalMemoryDataSource.add("1", "2", "server_memory", "서버 기억", 1, "admin")
    await LocalCore.llmUserMemoryDataSource.add("1", "2", "3", "사용자 기억", user_name="User")
    await LocalCore.llmServerStateDataSource.upsert(
        "1",
        "2",
        active_style_directive="반말",
        relationship_notes=["친근한 관계"],
        recent_summary="최근 요약",
    )
    await LocalCore.llmRecentMessageDataSource.add("1", "2", "3", "User", "user", "hello")

    consent = await LocalCore.llmConsentDataSource.get("1", "2", "3", "v1")
    server_state = await LocalCore.llmServerStateDataSource.get("1", "2")
    memories = await LocalCore.llmUserMemoryDataSource.list_for_users("1", "2", ["3"])
    recent = await LocalCore.llmRecentMessageDataSource.list_recent("1", "2", 10)

    assert consent.consented == 1
    assert server_state.active_style_directive == "반말"
    assert memories[0].user_name == "User"
    assert recent[0].content == "hello"
    assert not (tmp_path / "memory_state.json").exists()
