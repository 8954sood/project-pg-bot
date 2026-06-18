import os

import pytest

from core.llm.models import LLMBufferedMessage
from core.llm.tools import LLMToolRegistry
from core.llm.tools import web_search as ws
from core.llm.tools.base import ToolContext
from core.llm.tools.web_search import WebSearchTool

DDG_HTML = """
<div class="results">
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F1">First Result</a>
    <a class="result__snippet">First snippet text</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://example.com/2">Second Result</a>
    <a class="result__snippet">Second snippet text</a>
  </div>
</div>
"""

BING_HTML = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/page">Page Title</a></h2>
    <p>Page description text</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/other">Other Title</a></h2>
    <p>Other description text</p>
  </li>
</ol>
"""


def _ctx() -> ToolContext:
    return ToolContext(
        guild_id="g",
        channel_id="c",
        actor=LLMBufferedMessage(
            guild_id="g",
            channel_id="c",
            user_id="u",
            author_name="U",
            content="search the web",
            created_at="2026-06-18T00:00:00+00:00",
        ),
    )


def test_clean_text_unescapes_and_collapses_whitespace():
    assert ws._clean_text("a&nbsp;&amp;b  \n c") == "a &b c"
    assert ws._clean_text(None) == ""
    assert ws._clean_text("   ") == ""


def test_normalize_duckduckgo_url_handles_uddg_prefix_and_plain():
    assert ws._normalize_duckduckgo_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F1") == "https://example.com/1"
    assert ws._normalize_duckduckgo_url("/html/?q=test") == "https://duckduckgo.com/html/?q=test"
    assert ws._normalize_duckduckgo_url("https://open.example.com/x") == "https://open.example.com/x"
    assert ws._normalize_duckduckgo_url("") == ""


def test_parse_limit_clamps_and_defaults():
    assert ws._parse_limit({}) == ws._DEFAULT_LIMIT
    assert ws._parse_limit({"limit": 3}) == 3
    assert ws._parse_limit({"limit": 0}) == 1
    assert ws._parse_limit({"limit": 99}) == ws._MAX_LIMIT
    assert ws._parse_limit({"limit": "nope"}) == ws._DEFAULT_LIMIT


def test_parse_engines_defaults_filters_and_dedupes():
    assert ws._parse_engines({}) == ["duckduckgo"]
    assert ws._parse_engines({"engine": "bing"}) == ["bing"]
    assert ws._parse_engines({"engines": ["bing", "duckduckgo", "bing", "google"]}) == ["bing", "duckduckgo"]
    assert ws._parse_engines({"engines": ["google"]}) == ["duckduckgo"]


def test_dedupe_results_drops_empty_urls_and_duplicates_and_limits():
    items = [
        {"title": "a", "url": "https://a.com", "description": ""},
        {"title": "a2", "url": "https://a.com", "description": ""},  # dup url
        {"title": "b", "url": "   ", "description": ""},  # empty url
        {"title": "c", "url": "https://c.com", "description": ""},
    ]
    assert ws._dedupe_results(items, limit=10) == [
        {"title": "a", "url": "https://a.com", "description": ""},
        {"title": "c", "url": "https://c.com", "description": ""},
    ]
    assert len(ws._dedupe_results(items, limit=1)) == 1


def test_format_results_empty_and_normal():
    empty = ws._format_results("q", [])
    assert 'query="q"' in empty and "결과가 없습니다" in empty

    formatted = ws._format_results("q", [
        {"title": "T", "url": "https://t.com", "description": "D", "engine": "duckduckgo"},
    ])
    assert 'query="q"' in formatted
    assert "1. T" in formatted
    assert "https://t.com" in formatted
    assert "engine: duckduckgo" in formatted
    assert "D" in formatted


def test_duckduckgo_parser_extracts_results():
    parser = ws._DuckDuckGoHTMLParser()
    parser.feed(DDG_HTML)
    assert parser.results == [
        {"title": "First Result", "url": "https://example.com/1", "description": "First snippet text"},
        {"title": "Second Result", "url": "https://example.com/2", "description": "Second snippet text"},
    ]


def test_bing_parser_extracts_results():
    parser = ws._BingHTMLParser()
    parser.feed(BING_HTML)
    assert parser.results == [
        {"title": "Page Title", "url": "https://example.com/page", "description": "Page description text"},
        {"title": "Other Title", "url": "https://example.com/other", "description": "Other description text"},
    ]


def test_run_search_sync_aggregates_dedupes_and_records_errors(monkeypatch):
    monkeypatch.setattr(
        ws, "_search_duckduckgo", lambda query, limit: [
            {"title": "DDG", "url": "https://shared.com", "description": "ddg", "engine": "duckduckgo"},
        ]
    )
    monkeypatch.setattr(
        ws, "_search_bing", lambda query, limit: [
            {"title": "Bing dup", "url": "https://shared.com", "description": "bing", "engine": "bing"},
            {"title": "Bing unique", "url": "https://bing-only.com", "description": "bing2", "engine": "bing"},
        ]
    )

    out = ws._run_search_sync("q", ["duckduckgo", "bing"], limit=10)
    assert "https://shared.com" in out
    assert "https://bing-only.com" in out
    # duplicate URL kept once
    assert out.count("https://shared.com") == 1


def test_run_search_sync_reports_engine_errors(monkeypatch):
    def boom(query, limit):
        raise TimeoutError("slow")

    monkeypatch.setattr(ws, "_search_duckduckgo", lambda query, limit: [])
    monkeypatch.setattr(ws, "_search_bing", boom)

    out = ws._run_search_sync("q", ["duckduckgo", "bing"], limit=10)
    assert "일부 검색엔진 오류" in out
    assert "bing: 검색 시간 초과" in out


def test_web_search_tool_definition_shape_and_registration():
    tool = WebSearchTool()
    definition = tool.to_definition()
    assert definition["function"]["name"] == "web_search"
    assert "query" in definition["function"]["parameters"]["properties"]
    assert definition["function"]["parameters"]["required"] == ["query"]

    names = [d["function"]["name"] for d in LLMToolRegistry().tool_definitions()]
    assert "web_search" in names


@pytest.mark.asyncio
async def test_web_search_run_empty_query_returns_error():
    tool = WebSearchTool()
    out = await tool.run({"query": "   "}, _ctx())
    assert out.startswith("웹 검색 실패")


@pytest.mark.asyncio
async def test_web_search_run_returns_formatted_results(monkeypatch):
    monkeypatch.setattr(
        ws, "_search_duckduckgo", lambda query, limit: [
            {"title": "PyPI pytest", "url": "https://pypi.org/project/pytest", "description": "pytest pypi", "engine": "duckduckgo"},
        ]
    )
    monkeypatch.setattr(ws, "_search_bing", lambda query, limit: [])

    tool = WebSearchTool()
    out = await tool.run({"query": "python pytest", "limit": 3}, _ctx())
    assert 'query="python pytest"' in out
    assert "https://pypi.org/project/pytest" in out


@pytest.mark.asyncio
async def test_registry_dispatch_routes_to_web_search(monkeypatch):
    monkeypatch.setattr(
        ws, "_search_duckduckgo", lambda query, limit: [
            {"title": "T", "url": "https://t.com", "description": "d", "engine": "duckduckgo"},
        ]
    )
    monkeypatch.setattr(ws, "_search_bing", lambda query, limit: [])

    registry = LLMToolRegistry()
    out = await registry.dispatch("web_search", {"query": "hello", "limit": 1}, ctx=_ctx())
    assert "https://t.com" in out


@pytest.mark.asyncio
async def test_registry_dispatch_unknown_tool_returns_error():
    registry = LLMToolRegistry()
    out = await registry.dispatch("nope", {}, ctx=_ctx())
    assert out.startswith("알 수 없는 툴")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_WEB_SEARCH_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_LIVE_WEB_SEARCH_TESTS=1 to hit real DuckDuckGo/Bing",
)
async def test_live_web_search_returns_real_results():
    tool = WebSearchTool()
    out = await tool.run({"query": "python pytest documentation", "limit": 3}, _ctx())
    assert not out.startswith("웹 검색 실패")
    assert "url: http" in out