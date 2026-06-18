# project-pg-bot

## Discord LLM 메모리 봇

이 기능은 환경변수 `LLM_GUILD_CHANNEL_MAP`에 등록된 Discord 서버와 채널에서만 동작합니다. 형식은 `guild_id:channel_id,channel_id;guild_id:channel_id`입니다. 등록되지 않은 서버, 채널, DM, bot/webhook 메시지는 typing indicator, DB 저장, recent context, memory extraction, LLM 호출 대상이 아닙니다.

메시지는 이 Discord 서버/채널 안에서 LLM 봇의 응답 문맥과 메모리 기능을 제공하기 위한 용도로만 사용됩니다. 외부 분석, 광고, 별도 서비스 개선 목적으로 사용하지 않는다는 안내를 Discord Embed로 제공하고, 사용자는 버튼으로 동의 또는 비동의를 저장합니다. 동의하지 않은 사용자의 메시지는 LLM 처리와 장기 메모리 저장에서 제외합니다. 동의 버전은 `LLM_CONSENT_VERSION`으로 관리하며, 값이 바뀌면 다시 동의를 받을 수 있습니다.

전역 메모리는 서버 관리자만 `/llm-memory list`, `/llm-memory add`, `/llm-memory edit`, `/llm-memory delete`, `/llm-memory enable`, `/llm-memory disable` 명령으로 관리할 수 있습니다. 전역 메모리는 서버 전체 또는 특정 채널에 연결할 수 있습니다. 개인 메모리와 말투 요약은 memory extraction 결과로만 저장되며, 관리자 명령으로 직접 조회, 수정, 삭제하지 않습니다.

LLM 상태는 별도 JSON 파일이 아니라 기존 로컬 SQLite DB(`core/local/path.py`의 `db_path`)에 저장합니다. 저장 대상은 동의 상태, 전역 메모리, 개인 메모리, 말투 요약, 최근 메시지, memory job 상태입니다. `.memory_bot`, `memory_state.json`, `recent_messages.json` 같은 별도 state 파일은 만들지 않습니다.

허용된 채널에서 처리 대상 메시지를 받으면 먼저 typing indicator를 시작합니다. 채널 단위 debounce buffer가 짧은 시간의 메시지를 묶고, memory extraction job이 진행 중인 같은 guild/channel의 다음 main response는 보류합니다. 다른 채널은 독립적으로 응답할 수 있습니다.

LLM 설정은 `.env.example`을 참고하세요. `LLM_*` 값이 가장 우선이고, 그 다음 `OPENAI_*`, 마지막으로 generic `API_KEY`, `BASE_URL`, `MODEL` 값을 사용합니다. auxiliary memory extraction 설정이 비어 있으면 main LLM 설정을 fallback으로 사용합니다. payload logging은 기본 비활성화이며, 활성화해도 길이 제한과 API key 마스킹을 적용합니다.

테스트는 실제 Discord 접속 없이 mock/fake 객체를 사용합니다.

```bash
source .venv/bin/activate
pytest
```
