# PGVOICE 참조 보이스 형식 v1

Voice Studio에서 저장하는 전용 참조 음성 파일입니다. 확장자는 `.pgvoice`이며, 음성 데이터와 그 음성에서 실제로 말한 대사를 한 파일로 전달합니다. 문장은 고정하지 않습니다. 한국어·영어·일본어 대사와 해당 언어를 함께 저장합니다.

## 파일 구조

내부 컨테이너는 압축하지 않은 ZIP(STORE)이며 다음 두 파일만 허용합니다. PCM 음성이 최대 10초라 작고, 외부 ZIP 라이브러리나 CDN 없이 브라우저에서 생성할 수 있습니다.

```text
voice-reference.pgvoice
  manifest.json
  reference.wav
```

`manifest.json` 예시입니다. SHA-256 값은 실제 WAV 바이트 전체로 계산합니다.

```json
{
  "format": "pgvoice",
  "version": 1,
  "created_by": "project-pg-bot/voice-studio",
  "reference": {
    "prompt_text": "안녕하세요. 오늘은 기분 좋은 하루가 될 것 같아요.",
    "prompt_lang": "ko"
  },
  "audio": {
    "file": "reference.wav",
    "encoding": "pcm_s16le",
    "sample_rate": 32000,
    "channels": 1,
    "sample_count": 256000,
    "sha256": "여기에 실제 WAV 바이트 전체의 SHA-256 소문자 64자리"
  }
}
```

`reference.wav`는 표준 44바이트 RIFF/WAVE 헤더와 little-endian signed 16-bit PCM 데이터입니다. 샘플 수는 96,000~320,000개(3~10초), 채널은 1개, 샘플레이트는 32,000 Hz입니다. 내보내기는 오디오를 먼저 구간 단위로 자른 뒤 OfflineAudioContext로 리샘플링합니다. 자동 볼륨 정규화·시간 늘리기·모델 학습은 적용하지 않습니다.

`prompt_lang`은 `ko`, `en`, `ja` 중 하나입니다. `prompt_text`는 공백만으로 이루어지지 않은 최대 2,000자 문자열입니다. 음성을 자르거나 기존 파일을 가져온 경우 사용자가 실제 남은 음성에 맞춰 대사를 확인해야 합니다. 자동 음성 인식으로 일치 여부를 판정하지 않습니다.

## Python에서 읽기

검증 유틸은 `core/tts/reference_voice.py`에 있습니다. 표준 라이브러리만 사용하므로 추가 Python 의존성이 없습니다.

```python
from core.tts.reference_voice import (
    ReferenceVoiceError,
    load_reference_voice,
    parse_reference_voice,
)

# 로컬 파일
voice = load_reference_voice("voice-reference.pgvoice")
assert 3 <= voice.duration_seconds <= 10

# Discord 첨부 파일 수신 측에서 파일명/바이트를 전달
try:
    voice = parse_reference_voice(attachment_bytes, filename=attachment_filename)
except ReferenceVoiceError as error:
    # 파일을 수락하지 않고 UI에서 오류를 안내
    print(str(error))
else:
    wav_bytes = voice.audio
    prompt_text = voice.prompt_text
    prompt_lang = voice.prompt_lang
```

봇의 `/보이스등록`과 `/샘플보이스등록`은 `.pgvoice`만 수신하고 `SovitsService.register_package()`에서 이 검증을 통과한 `ReferenceVoice`를 처리합니다. 저장할 때는 Discord 사용자 ID 등에 기반해 서버가 정한 경로를 사용하세요. 패키지의 파일 경로를 추출하거나 저장 경로로 사용하지 않습니다.

검증 항목:

- 확장자 `.pgvoice` 및 파일 전체 최대 700,000바이트
- 내부 파일명이 `manifest.json`, `reference.wav`로 정확히 구성되며 중복·추가 파일 없음
- STORE 방식만 허용; 암호화, 디렉터리, 심볼릭 링크, 비정상 크기 거부
- manifest 최대 16,000바이트, 중복 JSON 키·잘못된 버전·허용하지 않은 필드 거부
- 언어·대사·PCM 인코딩·채널·샘플레이트·샘플 수의 형식 및 허용 범위
- WAV 헤더·실제 길이·샘플 수와 manifest의 일치
- 실제 WAV 전체의 SHA-256과 manifest 체크섬의 일치

ZIP 경로를 디스크로 추출하지 않으며, 내부 데이터를 읽기 전에 이름·크기·압축 방법부터 검사합니다. 로컬 파일도 최대 크기보다 한 바이트까지만 읽습니다. CRC 손상·잘못된 JSON·잘린 WAV·변조된 음성은 `ReferenceVoiceError`로 거부합니다.

## GPT-SoVITS-CPUFast와 연결할 때

확인한 로컬 소스의 `GPT_SoVITS/TTS_infer_pack/TTS.py`는 참조 음성을 16 kHz로 읽은 뒤 48,000~160,000샘플(3~10초) 범위를 검사합니다. 이 도구는 해당 길이 조건을 따르고 32 kHz WAV를 생성하므로 서버가 필요한 레이트로 읽을 수 있습니다.

`gpt_sovits_cpufast/api_v2.py`에서 필요한 참조 필드는 다음과 같습니다.

```python
reference_fields = {
    "ref_audio_path": server_selected_wav_path,
    "prompt_text": voice.prompt_text,
    "prompt_lang": voice.prompt_lang,
}
```

`ref_audio_path`는 브라우저 다운로드 경로가 아니라 GPT-SoVITS 서버에서 읽을 수 있는 경로여야 합니다. 검증한 `voice.audio`를 먼저 서버 측 파일로 저장한 뒤 전달해야 합니다. 위 값은 참조 설정이며 합성 요청에 필요한 `text`, `text_lang` 등은 별도로 지정합니다. 실제 모델 합성 품질은 녹음 환경과 모델에 따라 달라집니다.

## 형식 검증과 생성 출처

`.pgvoice` 확장자, `created_by` 식별자와 체크섬은 **형식·일관성·전송 손상을 검증하는 수단**입니다. GitHub Pages에서 실행되는 공개 JavaScript와 포맷은 누구나 재현할 수 있으므로, 이 검증만으로 실제 웹사이트에서 생성됐음을 인증할 수는 없습니다. 체크섬은 전자서명이 아니며, 대사와 음성이 실제로 일치함을 증명하지도 않습니다.

따라서 현재의 수락 조건은 “Voice Studio 전용 포맷을 정확히 따르는 유효한 파일”입니다. 실제 사이트 경유를 강제해야 한다면 추후 인증된 서버 업로드 및 서버 서명 발급/검증이 필요합니다. 정적 웹 코드에 비밀키를 넣지 않습니다.

## 현재 GPT-SoVITS 스트리밍 서버 연동

현재 봇은 `/v1/styles`에 검증한 `voice.audio`를 multipart `audio`로, `voice.prompt_text`와 `voice.prompt_lang`을 각각 `prompt_text`, `prompt_lang`으로 전송합니다. 참조 대사에는 이모지/발음 전처리를 적용하지 않습니다. 현재 서버의 참조 대사 한도가 1,000자이므로 파일 형식상 유효한 1,001~2,000자 대사는 등록 단계에서 거절합니다.

서버가 반환한 스타일 ID와 소유자·언어·공개 여부는 봇 DB에 보관합니다. 합성은 `/v1/tts/stream`에 해당 ID와 전처리한 문장, 음성의 언어를 보내며 WAV 경로를 직접 지정하지 않습니다. 서버에 WAV를 업로드하고 캐시하는 작업부터 삭제·합성까지 동일한 직렬화 잠금을 사용합니다.
