# ARM 서버의 봇·AR17+C24 연결

현재 운영 모델은 승인된 AR17+C24이며, TTS 서버는 모델의 원래 출력인
32 kHz / mono / s16le PCM을 스트리밍한다. 봇은 응답 헤더를 확인하고
FFmpeg로 48 kHz 모노로 변환한 뒤 Discord용 스테레오 프레임을 만든다.
48 kHz 서버 응답은 리샘플링 없이 같은 재생 큐에 전달한다.

## 환경 설정

- 봇 경로: `/home/ubuntu/code/project_pg`
- Python: 프로젝트 `.venv`의 3.12.16
- 시스템 패키지: `ffmpeg`와 Discord Opus 인코딩용 `libopus`
- 기존 `.env` 설정을 보존하고 `GPT_SOVITS_URL=http://127.0.0.1:9880` 설정
- 의존성 설치 시 `requirements.txt` 또는 `requirements-dev.txt`를 사용하여
  `constraints.txt`의 davey 0.1.7 고정을 함께 적용
- 토큰, SSH 키, `.env`, SQLite DB는 저장소에 포함하지 않음

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q
```

discord.py 2.7.1과 davey 0.2.0은 음성 암호화 키 전환 API가 맞지 않는다.
이 조합에서는 발화 표시가 켜지고 합성이 성공해도 수신자가 듣지 못할 수 있다.
현재는 davey 0.1.7을 사용하며, 자세한 이유는 README의 의존성 설명을 참고한다.
암호화 패키지를 변경한 뒤에는 봇을 재시작하고 음성 채널에 다시 연결한다.

## 서비스

봇은 `project-galaxy.service`, TTS는 `cpufast-ar17-c24.service`로 실행하며
둘 다 부팅 시 자동 시작한다. 봇의 systemd drop-in은 TTS 서비스에
`Wants`와 `After`를 설정한다. TTS API는 호스트의 `127.0.0.1:9880`에만 노출한다.
추론 컨테이너는 2 CPU, 메모리 8,000,000,000바이트, 추가 swap 없이 실행한다.

```bash
sudo systemctl status project-galaxy.service cpufast-ar17-c24.service
sudo systemctl restart cpufast-ar17-c24.service
sudo systemctl restart project-galaxy.service
```

TTS 서비스는 실행 중 벤치마크 잠금을 유지한다. 같은 서버에서 별도 모델
벤치마크를 실행하려면 먼저 TTS 서비스를 중지하고, 끝난 뒤 다시 시작한다.

## 확인 결과

2026-11-14 ARM 서버에서 전체 Python 테스트 388개 통과, 6개 건너뜀과
`pip check` 통과를 확인했다. 테스트는 32/48 kHz 응답의 스트리밍 재생,
홀수 바이트 경계, 짧은 파이프 읽기, 재생 취소와 자원 정리를 포함한다.
의존성 교체와 재연결 후 사용자가 실제 Discord 채널에서 Google TTS와
GPT-SoVITS가 모두 들리는 것을 확인했다.

사용자는 `/보이스등록`으로 참조 음성을 등록한 뒤 `/엔진변경`에서 GPT-SoVITS와
음성 ID를 선택한다. 기존 사용자의 엔진 설정이나 참조 음성을 일괄 변경하지 않는다.
