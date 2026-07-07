"""claude-auto-retry-herdr: herdr 안 Claude Code 세션의 레이트리밋 자동 재시도.

구현 언어는 python3(표준 라이브러리만). herdr integration 훅이 이미 python3를
하드 의존하므로 추가 런타임 없이 동작한다. CLI/훅 진입점은 얇은 bash 래퍼가
이 패키지를 호출한다.
"""

__version__ = "0.1.0"
