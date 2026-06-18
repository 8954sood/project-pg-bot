from __future__ import annotations

import asyncio
import html
import os
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

import aiohttp

from core.llm.tools.base import LLMTool, ToolContext, register_tool


_ALLOWED_ENGINES = {"duckduckgo", "bing"}
_DEFAULT_ENGINES = ["duckduckgo"]
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10
_TIMEOUT_SECONDS = float(os.getenv("LLM_WEB_SEARCH_TIMEOUT_SECONDS", "8"))


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_duckduckgo_url(url: str) -> str:
    if not url:
        return ""

    url = html.unescape(url)

    if url.startswith("//"):
        url = "https:" + url

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    # DuckDuckGo HTML results often wrap links as /l/?uddg=<encoded_url>
    uddg = query.get("uddg")
    if uddg:
        return urllib.parse.unquote(uddg[0])

    if url.startswith("/"):
        return "https://duckduckgo.com" + url

    return url


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        class_name = attr.get("class", "")

        if tag == "a" and "result__a" in class_name:
            self._current = {
                "title": "",
                "url": _normalize_duckduckgo_url(attr.get("href") or ""),
                "description": "",
            }
            self._capture = "title"
            self._buffer = []
            return

        if self._current is not None and "result__snippet" in class_name:
            self._capture = "description"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None or self._capture is None:
            return

        if self._capture == "title" and tag == "a":
            self._current["title"] = _clean_text("".join(self._buffer))
            self._capture = None
            self._buffer = []
            return

        if self._capture == "description" and tag in {"a", "div"}:
            self._current["description"] = _clean_text("".join(self._buffer))
            self._capture = None
            self._buffer = []

            if self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)

            self._current = None


class _BingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result = False
        self._in_title = False
        self._in_description = False
        self._current: dict[str, str] | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        class_name = attr.get("class", "")

        if tag == "li" and "b_algo" in class_name:
            self._in_result = True
            self._current = {"title": "", "url": "", "description": ""}
            return

        if not self._in_result or self._current is None:
            return

        if tag == "a" and not self._current["url"]:
            href = attr.get("href") or ""
            if href.startswith("http://") or href.startswith("https://"):
                self._current["url"] = html.unescape(href)
                self._in_title = True
                self._buffer = []
            return

        if tag == "p":
            self._in_description = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_description:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return

        if self._in_title and tag == "a":
            self._current["title"] = _clean_text("".join(self._buffer))
            self._in_title = False
            self._buffer = []
            return

        if self._in_description and tag == "p":
            self._current["description"] = _clean_text("".join(self._buffer))
            self._in_description = False
            self._buffer = []
            return

        if self._in_result and tag == "li":
            if self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)

            self._in_result = False
            self._in_title = False
            self._in_description = False
            self._current = None
            self._buffer = []


def _parse_limit(arguments: dict[str, Any]) -> int:
    raw_limit = arguments.get("limit", _DEFAULT_LIMIT)

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT

    return max(1, min(limit, _MAX_LIMIT))


def _parse_engines(arguments: dict[str, Any]) -> list[str]:
    raw_engines = arguments.get("engines")

    if raw_engines is None:
        raw_engines = arguments.get("engine")

    if raw_engines is None:
        return list(_DEFAULT_ENGINES)

    if isinstance(raw_engines, str):
        candidates = [raw_engines]
    elif isinstance(raw_engines, list):
        candidates = [str(engine) for engine in raw_engines]
    else:
        return list(_DEFAULT_ENGINES)

    engines: list[str] = []
    for engine in candidates:
        normalized = engine.strip().lower()
        if normalized in _ALLOWED_ENGINES and normalized not in engines:
            engines.append(normalized)

    return engines or list(_DEFAULT_ENGINES)


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.text(errors="replace")


async def _search_duckduckgo(
    session: aiohttp.ClientSession,
    query: str,
    limit: int,
) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    body = await _fetch_html(session, url)

    parser = _DuckDuckGoHTMLParser()
    parser.feed(body)

    results: list[dict[str, str]] = []
    for item in parser.results:
        results.append(
            {
                "title": item["title"],
                "url": item["url"],
                "description": item.get("description", ""),
                "source": "DuckDuckGo",
                "engine": "duckduckgo",
            }
        )
        if len(results) >= limit:
            break

    return results


async def _search_bing(
    session: aiohttp.ClientSession,
    query: str,
    limit: int,
) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    body = await _fetch_html(session, url)

    parser = _BingHTMLParser()
    parser.feed(body)

    results: list[dict[str, str]] = []
    for item in parser.results:
        results.append(
            {
                "title": item["title"],
                "url": item["url"],
                "description": item.get("description", ""),
                "source": "Bing",
                "engine": "bing",
            }
        )
        if len(results) >= limit:
            break

    return results


async def _search_engine(
    session: aiohttp.ClientSession,
    engine: str,
    query: str,
    limit: int,
) -> tuple[list[dict[str, str]], str | None]:
    try:
        if engine == "duckduckgo":
            return await _search_duckduckgo(session, query, limit), None

        if engine == "bing":
            return await _search_bing(session, query, limit), None

        return [], f"{engine}: 지원하지 않는 검색엔진입니다."

    except asyncio.TimeoutError:
        return [], f"{engine}: 검색 시간 초과"
    except aiohttp.ClientResponseError as exc:
        return [], f"{engine}: HTTP 오류 {exc.status}"
    except aiohttp.ClientError as exc:
        return [], f"{engine}: 네트워크 오류: {exc}"
    except Exception as exc:
        return [], f"{engine}: 검색 실패: {exc}"


def _dedupe_results(results: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    deduped: list[dict[str, str]] = []

    for item in results:
        url = item.get("url", "").strip()
        if not url or url in seen_urls:
            continue

        seen_urls.add(url)
        deduped.append(item)

        if len(deduped) >= limit:
            break

    return deduped


def _format_results(query: str, results: list[dict[str, str]], errors: list[str]) -> str:
    if not results:
        lines = [
            f'웹 검색 결과가 없습니다. query="{query}"',
            "검색엔진의 HTML 구조 변경, 일시적 차단, 네트워크 문제일 수 있습니다.",
        ]
    else:
        lines = [f'웹 검색 결과 query="{query}"']

        for index, item in enumerate(results, start=1):
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            description = item.get("description", "").strip()
            engine = item.get("engine", "").strip()

            lines.append(f"{index}. {title}")
            lines.append(f"   url: {url}")
            lines.append(f"   engine: {engine}")

            if description:
                lines.append(f"   description: {description}")

    if errors:
        lines.append("")
        lines.append("일부 검색엔진 오류:")
        lines.extend(f"- {error}" for error in errors)

    return "\n".join(lines)


async def _run_search(query: str, engines: list[str], limit: int) -> str:
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    connector = aiohttp.TCPConnector(limit_per_host=2)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
        connector=connector,
    ) as session:
        tasks = [
            _search_engine(
                session=session,
                engine=engine,
                query=query,
                limit=limit,
            )
            for engine in engines
        ]

        responses = await asyncio.gather(*tasks)

    all_results: list[dict[str, str]] = []
    errors: list[str] = []

    for results, error in responses:
        all_results.extend(results)
        if error:
            errors.append(error)

    deduped = _dedupe_results(all_results, limit)
    return _format_results(query, deduped, errors)


@register_tool
class WebSearchTool(LLMTool):
    name = "web_search"
    description = (
        "최신 정보, 외부 문서, 현재 이슈, 라이브러리/제품/가격/일정처럼 모델 지식만으로 답하면 "
        "오래됐을 수 있는 내용을 확인할 때 호출한다. query에는 실제 검색어를 넣고, limit은 필요한 "
        "결과 수를 1~10 사이로 넣는다. engines는 생략하면 duckduckgo를 사용하며, 필요하면 "
        "['duckduckgo', 'bing'] 중 하나 이상을 지정한다. 반환 결과의 URL과 설명을 근거로 MAIN LLM이 "
        "사용자에게 답변하되, 검색 결과가 항상 정답은 아니므로 중요한 내용은 여러 결과를 교차 확인한다."
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
            "engines": {
                "type": "array",
                "description": "사용할 검색엔진 목록. 생략 시 duckduckgo만 사용한다.",
                "items": {
                    "type": "string",
                    "enum": ["duckduckgo", "bing"],
                },
            },
        },
        "required": ["query"],
    }

    async def run(self, arguments: dict, ctx: ToolContext) -> str:
        query = _clean_text(str(arguments.get("query", "")))
        if not query:
            return "웹 검색 실패: query가 비어 있습니다."

        limit = _parse_limit(arguments)
        engines = _parse_engines(arguments)

        return await _run_search(query, engines, limit)