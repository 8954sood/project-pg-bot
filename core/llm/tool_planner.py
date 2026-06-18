import json
import logging
import re
from dataclasses import dataclass, field

from core.llm.config import LLMProviderConfig
from core.llm.llm_client import OpenAICompatibleClient
from core.llm.models import MemoryState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolPlan:
    tool: str  # "none" | "update_memory" | "update_style" | "clear_memory"
    scope: str  # "server" | "user"
    note: str = ""
    reason: str = ""

    @property
    def is_noop(self) -> bool:
        return self.tool in {"", "none"}


NONE_PLANS: list[ToolPlan] = [ToolPlan(tool="none", scope="user")]

VALID_TOOLS = {"none", "update_memory", "update_style", "clear_memory"}
VALID_SCOPES = {"server", "user"}

PLANNER_SYSTEM_PROMPT = (
    "너는 Discord 대화 봇의 툴 계획기다. 사용자의 최신 메시지를 보고 봇이 DB에 반영해야 할 툴을 0개 이상 선택한다. "
    "일반 대화, 인사, 질문, 잡담, 사실 확인은 툴 없이 그냥 대화로 처리한다(plan 없음). "
    "사용자가 명시적으로 저장/삭제를 요구한 경우에만 툴을 선택한다.\n\n"
    "툴 목록:\n"
    '- none: 툴이 필요 없음을 명시할 때 사용. 일반 대화/질문("있어?", "뭐야?", "알려줘")은 plan을 비우거나 none만 둔다.\n'
    '- update_memory: 사용자가 "기억해", "저장해", "메모해", "앞으로 ~라고 해줘" 식으로 장기 기억/선호/정보/규칙을 저장하라고 명시한 경우. '
    "note에는 봇이 실제 DB에 저장할 짧은 문장을 정리한다(사용자 원문 그대로보다 핵심만).\n"
    '- update_style: 사용자가 봇의 말투/어조/응답 방식을 "바꿔", "변경", "적용", "업데이트", "쓰게 해줘", "~로 답해줘" 식으로 변경하라고 명시한 경우. '
    "note에는 봇이 따를 말투 지시를 짧게 정리한다(예: '용용체로 답한다', '짧고 차분하게 답한다').\n"
    '- clear_memory: 사용자가 기억/말투를 "지워", "삭제", "초기화", "비워", "리셋" 하라고 명시한 경우. '
    "clear_memory는 다른 툴과 함께 쓰지 않는다.\n\n"
    "scope 규칙:\n"
    '- "서버", "길드", "채널", "전역", "공용", "모두", "기본 말투/기본 설정"이 명시되면 scope="server". '
    "관리자 권한 검사는 봇 실행 단계에서 하므로, 비관리자라도 요청이 서버 scope이면 그대로 \"server\"로 보고한다.\n"
    '- "나한테만", "내게만", "저한테만", "개인"이 명시되면 scope="user".\n'
    '- 그 외(명시 없음)는 scope="user"로 개인 저장한다.\n\n'
    "반드시 JSON object만 출력한다. 형식: "
    '{"plans": [{"tool": "update_memory|update_style|clear_memory", "scope": "server|user", "note": "저장할 내용"}], "reason": "짧은 이유"}\n'
    "일반 대화/질문이면 plans를 빈 배열로 둔다. 각 plan마다 tool/scope/note를 채운다."
)


class LLMToolPlanner:
    def __init__(self, client: OpenAICompatibleClient, config: LLMProviderConfig):
        self.client = client
        self.config = config

    async def plan(self, text: str, is_admin: bool, memory_state: MemoryState) -> list[ToolPlan]:
        if not text.strip():
            return NONE_PLANS
        user_prompt = self._build_user_prompt(text, is_admin)
        try:
            response = await self.client.chat(
                self.config,
                [
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception:
            logger.warning("Tool planner LLM call failed; defaulting to none", exc_info=True)
            return NONE_PLANS
        try:
            data = self._parse_json_object(response.content)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Tool planner returned invalid JSON; defaulting to none")
            return NONE_PLANS
        return self._to_plans(data)

    def _build_user_prompt(self, text: str, is_admin: bool) -> str:
        return (
            f"발화자 관리자 여부: {'관리자' if is_admin else '일반 유저'}\n"
            f"사용자 최신 메시지: {text}\n"
            "위 메시지에 대해 출력할 JSON만 답해라."
        )

    @staticmethod
    def _to_plans(data: dict) -> list[ToolPlan]:
        raw_plans = data.get("plans", [])
        if not isinstance(raw_plans, list):
            raw_plans = []
        plans: list[ToolPlan] = []
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", "none")).strip().lower()
            if tool not in VALID_TOOLS:
                tool = "none"
            scope = str(item.get("scope", "user")).strip().lower()
            if scope not in VALID_SCOPES:
                scope = "user"
            note = str(item.get("note", "") or "").strip()
            reason = str(item.get("reason", "") or "").strip()
            if tool in {"none", "clear_memory"}:
                note = ""
            plans.append(ToolPlan(tool=tool, scope=scope, note=note, reason=reason))
        actionable = [p for p in plans if not p.is_noop]
        if not actionable:
            return NONE_PLANS
        # clear_memory is exclusive: if present, drop everything else.
        clear = next((p for p in actionable if p.tool == "clear_memory"), None)
        if clear is not None:
            return [clear]
        return actionable

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