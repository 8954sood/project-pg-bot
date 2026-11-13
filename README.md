# project-pg-bot

## Discord LLM 메모리 봇

이 기능은 환경변수 `LLM_GUILD_CHANNEL_MAP`에 등록된 Discord 서버와 채널에서만 동작합니다. 형식은 `guild_id:channel_id,channel_id;guild_id:channel_id`입니다. 등록되지 않은 서버, 채널, DM, bot/webhook 메시지는 typing indicator, DB 저장, recent context, memory extraction, LLM 호출 대상이 아닙니다.

메시지는 이 Discord 서버/채널 안에서 LLM 봇의 응답 문맥과 메모리 기능을 제공하기 위한 용도로만 사용됩니다. 외부 분석, 광고, 별도 서비스 개선 목적으로 사용하지 않는다는 안내를 Discord Embed로 제공하고, 사용자는 버튼으로 동의 또는 비동의를 저장합니다. 동의하지 않은 사용자의 메시지는 LLM 처리와 장기 메모리 저장에서 제외합니다. 동의 버전은 `LLM_CONSENT_VERSION`으로 관리하며, 값이 바뀌면 다시 동의를 받을 수 있습니다.

전역 메모리는 서버 관리자만 `/llm-memory list`, `/llm-memory add`, `/llm-memory edit`, `/llm-memory delete`, `/llm-memory enable`, `/llm-memory disable` 명령으로 관리할 수 있습니다. 전역 메모리는 서버 전체 또는 특정 채널에 연결할 수 있습니다. 사용자는 `/llm-memory my-list`, `/llm-memory my-add`, `/llm-memory my-edit`, `/llm-memory my-delete` 명령으로 본인 개인 메모리만 관리할 수 있습니다. 개인 말투와 응답 포맷도 별도 테이블이 아니라 개인 메모리로 저장됩니다.

LLM 상태는 별도 JSON 파일이 아니라 기존 로컬 SQLite DB(`core/local/path.py`의 `db_path`)에 저장합니다. 저장 대상은 동의 상태, 전역 메모리, 개인 메모리, 최근 메시지입니다. `.memory_bot`, `memory_state.json`, `recent_messages.json` 같은 별도 state 파일은 만들지 않습니다.

허용된 채널에서 처리 대상 메시지를 받으면 먼저 typing indicator를 시작합니다. 채널 단위 debounce buffer가 짧은 시간의 메시지를 묶고, MAIN LLM이 필요할 때 tool call로 개인 메모리를 저장하거나 삭제합니다. 별도 memory extraction LLM job은 사용하지 않습니다.

LLM 설정은 `.env.example`을 참고하세요. `LLM_*` 값이 가장 우선이고, 그 다음 `OPENAI_*`, 마지막으로 generic `API_KEY`, `BASE_URL`, `MODEL` 값을 사용합니다. prompt에 포함할 최근 대화 메시지 수는 `LLM_MAX_RECENT_CONVERSATION_LINES`로 조정하며 기본값은 `12`입니다. LLM 서버로 보내는 요청 payload는 분석을 위해 항상 `logs/llm_requests.jsonl`에 JSONL로 기록되며, API key/Authorization 헤더는 기록하지 않습니다.

테스트는 실제 Discord 접속 없이 mock/fake 객체를 사용합니다.

```bash
source .venv/bin/activate
pytest
```

## 단일 언어 TTS용 발음 전처리 유틸

`core/tts/pronunciation.py`는 SoVITS 등 목표 언어가 정해진 모델에 넘기기 전에 혼합 문자열을 발음 표기로 바꾸는 **독립 유틸**입니다. 봇, 기존 텍스트 정규화, 엔진 요청에는 자동으로 적용되지 않습니다. 번역이나 TTS 합성을 실행하지 않습니다.

```bash
source .venv/bin/activate
python -m pip install -r requirements-pronunciation.txt
```

```python
from core.tts.language import analyze_language, is_language_dominant
from core.tts.pronunciation import PronunciationPreprocessor

profile = analyze_language("안녕하세요 hello")
assert profile.ratio("ko") == 0.5
assert not is_language_dominant("안녕하세요 hello", "ko")  # 50% 초과만 True
assert is_language_dominant("안녕하세요 hi", "ko", threshold=0.7)

processor = PronunciationPreprocessor.from_json("examples/tts_pronunciation.json")
processor.warmup()  # 선택: 첫 요청 전에 오프라인 사전 로드

result = processor.prepare("Python으로 日本語 test!", target_language="ko")
assert result.text == "파이썬으로 니혼고 테스트!"
assert result.complete

japanese = processor.prepare("안녕하세요 Python!", target_language="ja")
assert japanese.text == "アンニョンハセヨ パイソン!"

english = processor.prepare("안녕하세요 日本語", target_language="en")
assert english.text == "ahn nyung hah seh yo nihongo"
```

`prepare()`는 `PronunciationResult`를 반환합니다.

| 필드 | 의미 |
| --- | --- |
| `text` | 모델에 전달할 발음 표기 |
| `target_language` | 호출자가 지정한 `ko`, `en`, `ja` |
| `profile` | 원문을 NFC로 정규화한 뒤 집계한 `ko`, `en`, `ja`, `other` 문자 수와 `ratio()` |
| `changed` | 원문과 출력이 다른지 여부 |
| `unresolved` | 출력에 남은 목표 문자 체계 밖의 구간. 중복은 한 번만 표시 |
| `complete` | `unresolved`가 비어 있는지 여부. 실제 발음 정확도 평가값은 아님 |

비율 계산은 공백·숫자·기호·이모지를 제외하고 **모든 문자 체계의 글자**를 분모에 포함합니다. 빈 문자열과 글자가 없는 입력은 우세 언어가 없습니다. 한자는 일본어, Latin 문자는 영어로 간주하므로 중국어와 일본어, 영어와 다른 Latin 언어를 구분하는 언어 감지기는 아닙니다. 분해된 한글과 결합 악센트는 NFC 기준으로 계산합니다. `こんにちは hello`도 5글자 대 5글자이므로 일본어 비율은 정확히 50%입니다.

목표 언어는 모델 설정에 맞춰 직접 지정합니다. 다수 언어가 50%를 넘는지와 관계없이 외국 문자 구간을 각각 바꾸므로 `Python코드`처럼 붙어 있는 입력도 처리합니다. 기본 구간 변환에서는 목표 언어 글자, 공백, 구분 문장부호, 숫자, 이모지를 보존하고 전체 문자열에는 NFC 정규화를 적용합니다. `can't`처럼 영어 단어 안의 아포스트로피는 단어 발음에 포함해 처리합니다. 사용자가 지정한 사전 항목은 그 항목 전체를 대체합니다.

| 변환 | 처리 방식 |
| --- | --- |
| 한국어 → 영어 | koroman의 발음 규칙을 적용한 로마자 |
| 한국어 → 일본어 | koroman의 발음 규칙 및 음절 경계 보정 후 가타카나 근사 |
| 일본어 → 영어 | pykakasi의 한자·가나 읽기 → Hepburn 로마자 |
| 일본어 → 한국어 | pykakasi의 히라가나 읽기 → 한글 근사. 촉음·발음·장음 처리 |
| 영어 → 한국어/일본어 | CMUdict의 첫 ARPAbet 발음 → 한글/가타카나 근사 |

영어 대문자 약어와 사전에 없는 ASCII 단어는 알파벳 이름으로 읽습니다. 예를 들어 `AI`는 `에이아이`/`エーアイ`입니다. 등록되지 않은 고유명사를 철자로 읽는 것을 피하려면 발음 사전에 추가하세요. 단독 한글 자음·모음은 글자 이름으로 읽으며, 조합되지 않은 자모의 연속을 자동으로 단어로 복원하지는 않습니다. 변환에는 네트워크 요청이나 모델 다운로드가 없습니다. 의존성은 해당 변환을 처음 실행할 때 로드합니다.

사전은 `목표 언어 → 원문 → 원하는 발음` 형식입니다. 예제 파일을 복사하거나 생성자에 직접 전달할 수 있습니다.

```python
processor = PronunciationPreprocessor({
    "ko": {"Python": "파이썬", "こんにちは": "곤니치와"},
    "en": {"안녕하세요": "ahn nyung hah seh yo"},
    "ja": {"Python": "パイソン"},
})
```

긴 항목을 먼저 매칭하고, 사전 결과를 다시 치환하지 않습니다. ASCII 영문은 대소문자를 구분하지 않으며 영문 단어 내부에는 매칭하지 않습니다. `AI`는 `said`에 적용되지 않고 `AI에서`에는 적용됩니다. 한글·일본어 항목은 조사나 접미사가 붙은 단어 내부에서도 매칭할 수 있습니다. 문장 전체나 여러 단어로 된 항목도 지원합니다. 사전 값은 목표 언어 문자 체계여야 하며 빈 값은 허용하지 않습니다. 사전은 생성 시 복사하며, 변경하려면 새 인스턴스를 만드세요.

일본어 한자 읽기와 `は/へ` 등의 문맥별 발음은 항상 정확하지 않습니다. 예제 사전은 `こんにちは`의 읽기를 보정합니다. 영어 TTS의 로마자 해석, 일본어에 없는 받침, 영어 모음·자음의 외래어 표기도 모델마다 다릅니다. 모델별 사전으로 발음 표기를 조정하고 실제 합성 음성으로 검증해야 합니다. 로마자/가타카나 치환만으로 원래 언어의 억양과 발음을 보장하지 않습니다.

```python
result = processor.prepare("안녕 Русский", "ko")
assert result.text == "안녕 Русский"
assert result.unresolved == ("Русский",)
# complete가 False인 입력을 합성할지, 거절할지, 다른 엔진으로 보낼지는 호출자가 결정
```

미지원 문자와 변환되지 않은 글자는 삭제하지 않고 `unresolved`로 알립니다. 잘못된 언어/사전 설정은 `ValueError`, 의존성 누락과 백엔드 오류는 예외로 전달됩니다. 예외 발생 결과는 캐시하지 않습니다.

성능을 위해 인스턴스를 재사용하세요. 기본 LRU 캐시는 변환 방향별 구간 결과를 합쳐 최대 2,048개, 원문 길이 128자 이하 구간만 보관합니다. 문장 전체는 캐시하지 않으므로 긴 입력이 캐시를 점유하지 않습니다. `cache_size=0`으로 끄거나 `max_cached_run_length`로 길이 한도를 조절하고, `cache_info()`/`clear_cache()`로 확인·초기화할 수 있습니다. 사전은 인스턴스별로 한 번 로드하고, 일본어 리더는 스레드 간 접근을 직렬화합니다. 동시 캐시 미스에서는 같은 변환이 중복 실행될 수 있습니다.

이 API는 동기식입니다. asyncio 기반 호출자가 사전 로딩이나 변환으로 이벤트 루프를 막지 않으려면 다음처럼 실행하세요.

```python
import asyncio

await asyncio.to_thread(processor.warmup)
result = await asyncio.to_thread(processor.prepare, "Python 日本語", "ko")
```

다음 명령은 합성 없이 비율 분석, 구간 캐시를 끈 변환, 같은 구간을 반복한 캐시 사용 변환의 중앙값/p95 및 사전 초기화 시간을 측정합니다. `--target`은 `ko`, `en`, `ja` 중 선택할 수 있습니다. 입력 내용과 실행 환경에 따라 결과가 달라지며 캐시를 끈 경우에도 라이브러리 내부 캐시는 남아 있습니다.

```bash
python -m benchmarks.tts_pronunciation --samples 1000 --target ko
python -m pytest tests/core/tts -q
```

사용한 백엔드 문서: [koroman Python API](https://github.com/gerosyab/koroman#-getting-started), [pykakasi convert API](https://pykakasi.readthedocs.io/en/latest/api.html), [CMUdict](https://github.com/prosegrinder/python-cmudict).
