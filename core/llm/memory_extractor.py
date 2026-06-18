import json
import logging
import re

from core.llm.config import LLMProviderConfig
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.models import LLMBufferedMessage, MemoryExtractionResult

logger = logging.getLogger(__name__)


class LLMMemoryExtractor:
    def __init__(self, client: OpenAICompatibleClient, config: LLMProviderConfig):
        self.client = client
        self.config = config

    async def extract(self, messages: list[LLMBufferedMessage]) -> MemoryExtractionResult:
        transcript = "\n".join(f"user_id={message.user_id} name={message.author_name}: {message.content}" for message in messages)
        schema = {
            "active_style_directive": "string",
            "server_style_summary": "string",
            "server_memory_add": ["string"],
            "user_memory_add": [{"user_id": "string", "user_name": "string", "note": "string"}],
            "user_style_add": [{"user_id": "string", "user_name": "string", "note": "string"}],
            "user_style_phrases_add": [{"user_id": "string", "user_name": "string", "phrases": ["string"]}],
            "relationship_notes_add": ["string"],
        }
        prompt = (
            "너는 대화 봇의 메모리 추출기다. "
            "대화에서 장기적으로 저장할 사용자 선호, 말투 지시, 서버 말투, 반복 주제만 추출한다. "
            "민감한 개인정보, 일회성 농담, 공격적 표현의 직접 모방은 저장하지 않는다. "
            "반드시 JSON object만 출력한다.\n\n"
            f"schema={json.dumps(schema, ensure_ascii=False)}\n"
            "저장 범위 규칙:\n"
            "- 기본값은 개인 저장이다. 특정 사용자가 말한 선호, 호칭, 말투, 금지사항, 응답 방식은 user_memory_add 또는 user_style_add에 저장한다.\n"
            "- '앞으로 특정 말투로 답해줘'처럼 봇에게 말투를 요청한 문장은, 서버/길드/모두/전체/채널 전체가 명시되지 않으면 해당 발화자 개인 말투로 저장한다.\n"
            "- '나한테만', '내게만', '저한테만', '개인적으로', '길드에는 적용하지마', '서버에는 적용하지마'가 있으면 반드시 개인 말투로 저장하고 active_style_directive/server_style_summary는 비워 둔다.\n"
            "- active_style_directive와 server_style_summary는 사용자가 명시적으로 서버 전체, 길드 전체, 모두, 채널 전체에 적용하라고 말한 경우에만 사용한다.\n"
            "- server_memory_add는 서버/길드/채널 전체에 공유되어도 되는 사실이나 관리자가 명시한 전역 기억에만 사용한다.\n"
            "- user_id는 transcript에 나온 author_id만 사용한다. 모르는 사용자를 만들지 않는다.\n"
            "- 저장할 새 정보가 없으면 빈 문자열 또는 빈 배열을 사용한다.\n\n"
            + transcript
        )
        response = await self.client.chat(self.config, [{"role": "user", "content": prompt}])
        try:
            data = self._parse_json_object(response.content)
            self._validate_payload(data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Memory extraction returned invalid JSON")
            return MemoryExtractionResult("", "", [], [], [], [], [], False)
        user_memories = [
            (str(item.get("user_id", "")).strip(), str(item.get("user_name", "")).strip(), str(item.get("note", "")).strip())
            for item in self._dict_list(data.get("user_memory_add"))
            if item.get("user_id") and item.get("note")
        ]
        user_styles = [
            (str(item.get("user_id", "")).strip(), str(item.get("user_name", "")).strip(), str(item.get("note", "")).strip())
            for item in self._dict_list(data.get("user_style_add"))
            if item.get("user_id") and item.get("note")
        ]
        style_phrases = [
            (
                str(item.get("user_id", "")).strip(),
                str(item.get("user_name", "")).strip(),
                self._string_list(item.get("phrases")),
            )
            for item in self._dict_list(data.get("user_style_phrases_add"))
            if item.get("user_id") and self._string_list(item.get("phrases"))
        ]
        server_memory = self._string_list(data.get("server_memory_add"))
        relationship_notes = self._string_list(data.get("relationship_notes_add"))
        active_style = str(data.get("active_style_directive") or "").strip()
        server_style = str(data.get("server_style_summary") or "").strip()
        return MemoryExtractionResult(
            active_style,
            server_style,
            server_memory,
            relationship_notes,
            user_memories,
            user_styles,
            style_phrases,
            bool(active_style or server_style or server_memory or relationship_notes or user_memories or user_styles or style_phrases),
        )

    @staticmethod
    def _parse_json_object(content: str) -> dict:
        text = content.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        return data

    @staticmethod
    def _validate_payload(payload: dict) -> None:
        allowed = {
            "active_style_directive",
            "server_style_summary",
            "server_memory_add",
            "user_memory_add",
            "user_style_add",
            "user_style_phrases_add",
            "relationship_notes_add",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported memory fields: {sorted(unknown)}")

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _dict_list(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]
