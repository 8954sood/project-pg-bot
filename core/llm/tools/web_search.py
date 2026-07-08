from __future__ import annotations

import asyncio
import html
import os
import re
from typing import Any

import aiohttp

from core.llm.tools.base import LLMTool, ToolContext, register_tool


_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10
_FIRECRAWL_API_KEY_ENV = "FIRECRAWL_API_KEY"
_FIRECRAWL_BASE_URL = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev").rstrip("/")
_TIMEOUT_SECONDS = float(os.getenv("LLM_WEB_SEARCH_TIMEOUT_SECONDS", "20"))


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _truncate(value: str, max_chars: int) -> str:
    value = _clean_text(value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _parse_limit(arguments: dict[str, Any]) -> int:
    raw_limit = arguments.get("limit", _DEFAULT_LIMIT)

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT

    return max(1, min(limit, _MAX_LIMIT))


def _get_firecrawl_api_key() -> str:
    return os.getenv(_FIRECRAWL_API_KEY_ENV, "").strip()


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    """Extract Firecrawl search results from common response shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]

    results = payload.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]

    return []


def _metadata_text(item: dict[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return ""

    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return ""


def _item_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return _metadata_text(item, *keys)


def _format_results(query: str, results: list[dict[str, Any]], limit: int) -> str:
    if not results:
        return "\n".join(
            [
                f'Firecrawl 웹 검색 결과가 없습니다. query="{query}"',
                "검색어가 너무 좁거나, Firecrawl API 오류/일시적 제한/네트워크 문제일 수 있습니다.",
            ]
        )

    lines = [f'Firecrawl 웹 검색 결과 query="{query}"']

    for index, item in enumerate(results[:limit], start=1):
        title = _clean_text(_item_text(item, "title", "name"))
        url = _clean_text(_item_text(item, "url", "sourceURL", "sourceUrl", "source_url", "link"))
        description = _clean_text(
            _item_text(item, "description", "snippet", "summary", "excerpt")
        )
        markdown = _clean_text(_item_text(item, "markdown", "content", "text"))

        if not title and url:
            title = url

        lines.append(f"{index}. {title or '제목 없음'}")

        if url:
            lines.append(f"   url: {url}")

        if description:
            lines.append(f"   description: {_truncate(description, 500)}")

        if markdown:
            lines.append(f"   markdown_preview: {_truncate(markdown, 1200)}")

    return "\n".join(lines)


async def _firecrawl_search(query: str, limit: int) -> str:
    api_key = _get_firecrawl_api_key()
    if not api_key:
        return (
            "Firecrawl 웹 검색 실패: FIRECRAWL_API_KEY 환경변수가 비어 있습니다. "
            "Firecrawl API 키를 .env 또는 실행 환경에 설정하세요."
        )

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "query": query,
        "limit": limit,
    }
    url = f"{_FIRECRAWL_BASE_URL}/v2/search"

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(url, json=body) as response:
                response_text = await response.text()

                if response.status == 401:
                    return "Firecrawl 웹 검색 실패: API 키가 유효하지 않거나 권한이 없습니다."

                if response.status == 429:
                    return "Firecrawl 웹 검색 실패: Firecrawl API rate limit에 걸렸습니다."

                if response.status >= 400:
                    return (
                        f"Firecrawl 웹 검색 실패: HTTP {response.status}\n"
                        f"response: {_truncate(response_text, 1000)}"
                    )

                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    return (
                        "Firecrawl 웹 검색 실패: JSON 응답을 파싱하지 못했습니다.\n"
                        f"response: {_truncate(response_text, 1000)}"
                    )

    except asyncio.TimeoutError:
        return "Firecrawl 웹 검색 실패: 요청 시간이 초과되었습니다."
    except aiohttp.ClientError as exc:
        return f"Firecrawl 웹 검색 실패: 네트워크 오류: {exc}"
    except Exception as exc:
        return f"Firecrawl 웹 검색 실패: {exc}"

    results = _extract_results(payload)
    return _format_results(query, results, limit)


@register_tool
class WebSearchTool(LLMTool):
    name = "web_search"
    description = (
        "최신 정보, 외부 문서, 현재 이슈, 라이브러리/제품/가격/일정처럼 모델 지식만으로 답하면 "
        "오래됐을 수 있는 내용을 확인할 때 호출한다. Firecrawl API를 사용해 웹 검색 결과와 "
        "페이지 내용을 가져온다. query에는 실제 검색어를 넣고, limit은 필요한 결과 수를 1~10 사이로 넣는다. "
        "반환 결과의 URL, 설명, markdown_preview를 근거로 MAIN LLM이 사용자에게 답변하되, "
        "검색 결과가 항상 정답은 아니므로 중요한 내용은 여러 결과를 교차 확인한다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "웹에 검색할 문장 또는 키워드. 한국어/영어 모두 가능하다.",
            },
            "limit": {
                "type": "integer",
                "description": "가져올 최대 검색 결과 수. 1~10 사이 권장. 기본값은 5.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    async def run(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        query = _clean_text(str(arguments.get("query", "")))
        if not query:
            return "Firecrawl 웹 검색 실패: query가 비어 있습니다."

        limit = _parse_limit(arguments)
        return await _firecrawl_search(query, limit)
