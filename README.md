# project-pg-bot

## 실행 환경과 의존성

Python **3.11 이상**을 사용하며, 로컬 검증 환경은 Python 3.12입니다. 2026-11-13 기준 호환 가능한 안정 버전으로 직접·간접 Python 의존성을 갱신했습니다. `constraints.txt`는 실제 검증한 버전을 고정하며, 선택 기능이나 개발 패키지를 설치 대상으로 추가하지는 않습니다.

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip check
pytest
```

주요 갱신은 Pillow 13.0.0, python-dotenv 1.2.4, websockets 17.4, pytest 9.2.0, pytest-asyncio 1.5.0, aiohttp 3.14.5, davey 0.2.0입니다. discord.py 2.7.1, aiosqlite 0.22.1, gTTS 2.5.4와 발음 전처리 패키지는 이미 최신 버전이라 유지했습니다. discord.py의 `PyNaCl < 1.6`, gTTS의 `click < 8.2` 조건 때문에 두 의존성은 각각 1.5.0과 8.1.8로 유지합니다. Python 3.10 지원 종료는 [websockets 17 변경 사항](https://websockets.readthedocs.io/en/stable/project/changelog.html)과 [Pillow 13 변경 사항](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html#python-3-10)을 참고하세요.

`Deprecated`는 Python 3.12 이상에서 3.0.1, Python 3.11에서는 해당 Python을 지원하는 최신 버전인 1.3.1을 사용하도록 환경 마커로 분리했습니다.

## Voice Studio 정적 웹

`web/voice-reference/`는 빌드나 서버 API 없이 GitHub Pages에 올릴 수 있는 참조 보이스 제작 도구입니다. 마이크 녹음, 기존 음성 가져오기, 파형 구간 선택, 양끝 무음 줄이기, 미리 듣기를 지원합니다. 한국어·영어·일본어의 기본/짧은 문장을 고르거나 직접 대사를 수정할 수 있습니다. 음성과 대사는 서버에 전송하거나 브라우저 저장소에 영구 보관하지 않습니다.

저장은 선택한 음성과 실제 대사·녹음 언어를 묶은 전용 **`.pgvoice` 파일**로 합니다. 참조 구간은 3~10초로 제한하며 WAV는 32 kHz, 모노, 16-bit PCM으로 변환합니다. 최대 녹음은 30초, 가져올 파일은 20 MB·2분 이하입니다. 마이크는 HTTPS 또는 localhost에서 사용할 수 있습니다. 브라우저가 디코딩할 수 없는 파일은 읽을 수 없다는 안내를 표시합니다.

```bash
source .venv/bin/activate
python -m http.server 8765 --bind 127.0.0.1
# http://127.0.0.1:8765/web/voice-reference/
```

GitHub 저장소의 **Settings → Pages → Build and deployment → Source: GitHub Actions**를 선택하세요. `.github/workflows/voice-reference-pages.yml`이 관련 파일의 `main` 변경 또는 수동 실행 시 JavaScript 테스트를 통과한 뒤 `web/voice-reference/`만 배포합니다. PR에서는 검사만 수행합니다. 워크플로는 [GitHub Pages 공식 배포 방식](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)을 사용하며 별도 토큰이나 서비스 비밀키는 필요하지 않습니다. 자산 경로가 상대 경로라 프로젝트 Pages의 하위 경로에서도 작동합니다.

```bash
node --test tests/web/*.test.mjs
```

실제 마이크를 사용하지 않는 브라우저 오디오 검사는 로컬 서버의 `/tests/web/browser-audio.html`에서 실행할 수 있습니다. 합성 음성을 MediaStream으로 만들어 실제 MediaRecorder 녹음·디코딩·리샘플링·패키징을 검사하며 스피커에 연결하지 않습니다. 이 검사 페이지는 배포 대상 폴더 밖에 있습니다.

`.pgvoice` 구조, 검증 규칙, 향후 봇에서 파일을 읽는 방법은 [참조 보이스 파일 형식 문서](docs/reference-voice-format.md)를 참고하세요. Discord의 `/보이스등록`과 `/샘플보이스등록`은 안내·이름·파일 첨부 모달을 열고, 이 검증 유틸로 파일을 확인한 뒤 GPT-SoVITS 서버에 등록합니다.

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

`core/tts/pronunciation.py`는 SoVITS 등 목표 언어가 정해진 모델에 넘기기 전에 혼합 문자열을 발음 표기로 바꾸는 **독립 유틸**입니다. GPT-SoVITS 합성 요청에서는 선택한 음성의 언어에 맞춰 자동 적용하며, gTTS에는 발음 변환을 적용하지 않습니다. 번역이나 TTS 합성을 실행하지 않습니다.

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

## TTS 이모지 전처리 유틸

`core/tts/emoji_normalizer.py`의 `normalize_tts_emojis()`는 Discord 런타임이나 음성 엔진에 의존하지 않는 독립 유틸입니다. `requirements-pronunciation.txt`로 설치하는 [emoji 2.16.0](https://pypi.org/project/emoji/2.16.0/)의 내장 언어 데이터를 사용하므로 변환 중 네트워크 요청이 없습니다. 한국어(`ko`), 영어(`en`), 일본어(`ja`) 중 **TTS 모델의 목표 언어**를 지정하세요.

```python
from core.tts.emoji_normalizer import normalize_tts_emojis
from core.tts.pronunciation import PronunciationPreprocessor

assert normalize_tts_emojis("고마워 ❤️!", "ko") == "고마워 하트!"
assert normalize_tts_emojis("❤️👍🏽", "en") == "heart thumbs up"
assert normalize_tts_emojis("❤️👍🏽", "ja") == "ハート サムズアップ"
assert normalize_tts_emojis("좋아요 <:pepe:123456789>", "ko") == "좋아요"
assert normalize_tts_emojis("20% 할인! A+B=3 © ™ ↔", "ko") == "20% 할인! A+B=3 © ™ ↔"

# 먼저 이모지를 처리하고, 남은 외국어를 기존 발음 유틸로 변환합니다.
target = "ko"
text = normalize_tts_emojis("test ❤️ <:pepe:123456789>", target)
if text.strip():
    result = PronunciationPreprocessor().prepare(text, target)
    assert result.text == "테스트 하트"
# 커스텀 이모지만 있는 입력은 빈 문자열이므로 합성을 생략할 수 있습니다.
```

처리 규칙:

- Discord 커스텀 이모지 `<:name:ID>`, `<a:name:ID>`는 이름과 숫자를 읽지 않도록 제거합니다. 제거로 앞뒤 단어가 붙지 않게 공백을 둡니다.
- 일반 유니코드 이모지는 목표 언어의 이름으로 바꿉니다. 하트·엄지 척·미소·손 흔들기 등 자주 쓰는 이모지는 짧은 이름을 사용하고, 나머지는 라이브러리의 내장 이름을 사용합니다. 반복 이모지는 각각 읽습니다.
- 피부색은 생략합니다. 직업·가족·국기·지역 깃발 등 조합을 인식하며, 가족은 짧게 `가족`/`family`/`家族`으로 읽습니다. 키캡 `1️⃣`는 `1`, `#️⃣`는 `#`로 바꿉니다.
- 일반 숫자와 문장부호·기호는 유지합니다. `©`, `™`, `↔`, `❤`, `☀`처럼 문자로도 쓰이는 기호는 그대로 두고, `©️`, `❤️`, `☀️`처럼 이모지 표시(VS16)가 붙은 경우에 이름으로 읽습니다. 문자 표시(VS15)를 명시한 입력은 유지합니다.
- 치환이 있으면 수평 공백을 정리하며 CR/LF 줄바꿈은 유지합니다. 치환할 항목이 없으면 원문을 그대로 반환합니다. 제거 후 공백만 남으면 빈 문자열을 반환합니다.
- 목표 언어 이름이 아직 없거나 비표준 ZWJ 조합의 기본 이름을 찾을 수 없으면 `이모지`/`emoji`/`絵文字`로 읽습니다. 라이브러리가 인식하지 못하는 신규 문자 자체는 보존합니다.

이 함수는 나머지 문장을 번역하거나 발음을 치환하지 않습니다. 봇의 gTTS(ko/en/ja)와 GPT-SoVITS 합성 요청에 적용합니다. `.pgvoice` 참조 대사는 변환하지 않습니다. 참조 대사는 실제 녹음 내용과 일치하게 유지해야 합니다. 최초 호출에서 필요한 언어 데이터만 로드해 재사용하며, 사용자 메시지 전체를 캐시하지 않습니다.

검증 명령: `python -m pytest tests/core/tts/test_emoji_normalizer.py -q`. 라이브러리 API와 데이터 설명은 [공식 문서](https://carpedm20.github.io/emoji/docs/index.html)를 참고하세요.

## GPT-SoVITS 음성

`GPT_SOVITS_URL`을 GPT-SoVITS 스트리밍 서버 주소로 설정합니다. 기본 엔진은 gTTS입니다.
`/보이스등록`을 실행하면 Voice Studio 링크와 제작 안내, 이름 입력, 파일 첨부가 한 모달에 표시됩니다. 안내 링크에서 녹음/구간 선택 후 `.pgvoice`로 저장하고 같은 모달에 파일을 첨부해 제출합니다. 별도의 안내 명령을 실행할 필요가 없습니다. 파일의 WAV 헤더·길이·해시·대사·언어를 검증한 뒤 서버에 전송하므로 대사와 언어를 따로 입력하지 않습니다. 일반 WAV/ZIP는 받지 않습니다. 개인 음성은 한 개이며 처음에는 비공개입니다. `/보이스공개`로 공개 여부를 변경합니다. `/보이스삭제`는 본인이 삭제할 수 있는 음성을 목록으로 보여주며, 이름을 선택하고 삭제 확인 버튼을 누르면 서버의 음성까지 삭제합니다. 봇 소유자는 `/샘플보이스등록`으로 공개 샘플을 여러 개 등록할 수 있습니다. 서버에 이미 등록된 스타일은 `/샘플보이스가져오기`로 추가할 수 있습니다. `/보이스선택`과 `/보이스목록`은 내 음성과 공개 음성을 이름·언어가 있는 선택 메뉴로 보여줍니다. 25개를 넘으면 페이지를 나누며 스타일 ID를 입력할 필요가 없습니다. 등록 직후에도 선택 메뉴가 표시됩니다. `/엔진변경`에서 구글 TTS를 선택하면 즉시 전환하고, GPT-SoVITS를 선택하면 같은 음성 선택 메뉴를 엽니다. 기존 `/tts_engine` 명령도 사용할 수 있습니다.

서버의 등록·삭제·합성은 봇 전체에서 직렬 처리하고 외부 클라이언트가 사용 중이면 잠시 재시도합니다. 사용 권한이 사라진 음성이나 서버 오류는 gTTS로 대체합니다. 음성 소유권과 공개 설정은 봇의 SQLite에, 음성 데이터는 GPT-SoVITS 서버에 저장되므로 둘 다 백업해야 합니다. 서버 API는 인증을 제공하지 않으므로 신뢰할 수 있는 네트워크에서만 접근하도록 구성하세요.

합성 전처리는 다음 순서로 적용합니다. 대기열에는 원문과 문장부호를 보관하고 Discord 커스텀 이모지는 제거합니다. gTTS는 사용자 언어(ko/en/ja)에 맞는 이모지 이름과 기존 점/물음표 처리를 적용합니다. GPT-SoVITS는 선택한 음성의 언어로 이모지 이름을 바꾸고 외국어 구간의 발음을 변환하며 문장부호를 보존합니다. 변환할 수 없는 문자가 남거나 서버의 2,000자 한도를 넘으면 원문으로 gTTS를 시도합니다. 읽을 내용이 없어지면 합성을 생략합니다. 참조 녹음의 대사는 그대로 유지하며, 현재 GPT-SoVITS 서버의 대사 한도(1,000자)를 넘는 패키지는 안내 후 거절합니다.

`VOICE_STUDIO_URL`로 녹음 안내 링크를 바꿀 수 있습니다. 기본값은 `https://8954sood.github.io/project-pg-bot/`입니다. `TTS_PRONUNCIATION_DICTIONARY`에 `examples/tts_pronunciation.json` 형식의 사전 경로를 지정하면 GPT-SoVITS 발음 표기를 보정합니다. 발음 변환 인스턴스와 구간 캐시를 재사용하고, 사전 로드와 변환은 합성 요청 처리 시 별도 스레드에서 실행합니다. 필요한 이모지/발음 패키지는 `requirements.txt`에 포함되어 있습니다.

GPT-SoVITS 오디오는 서버의 **48 kHz / mono / s16le PCM**을 직접 수신합니다. 이 경로에는 FFmpeg나 리샘플링이 없으며, Discord가 요구하는 20ms·48 kHz·스테레오 프레임에 맞춰 채널만 복제합니다. 전송 경계에서 잘린 PCM 샘플과 프레임을 이어 붙이고 마지막 프레임에만 무음을 채웁니다. 다음 문장 합성을 기다리는 동안에는 부분 샘플을 보존한 채 무음 프레임을 보내 Discord가 뒤늦게 음성 패킷을 몰아서 전송하지 않도록 합니다. 첫 재생 프레임이 준비된 뒤 Discord 재생을 시작하며, 버퍼는 8프레임(160ms)으로 제한해 HTTP 수신에 역압을 적용합니다. 재생 중단 시 버퍼 대기와 HTTP 연결을 종료하고 공통 잠금을 반환합니다. gTTS의 MP3 재생에는 기존 FFmpeg를 사용합니다.
