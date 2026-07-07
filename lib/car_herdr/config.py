"""설정 로더. 모든 필드 optional, 누락 시 기본값 폴백.

`~/.config/car-herdr/config.json` (경로는 paths.config_file). 파일이 없거나
깨졌으면 조용히 기본값으로 동작한다(자동 재시도 도구가 설정 오류로 죽으면 안 됨).
"""

import json

from . import paths

DEFAULTS = {
    # 재시도 상한 (마커 파일당 누적)
    "maxRetries": 10,
    # 리셋 시각 이후 추가로 더 기다릴 여유 (초)
    "marginSeconds": 120,
    # 리셋 시각 파싱 실패 시 대기 시간 (시간)
    "fallbackWaitHours": 5,
    # 대기 프로세스 폴링 간격 (초)
    "pollIntervalSeconds": 5,
    # watch 데몬 폴링 간격 (초). 훅이 못 잡은 레이트리밋을 이 주기로 재확인
    "watchIntervalSeconds": 20,
    # 재시도 때 주입할 메시지
    "retryMessage": "Continue where you left off. The previous attempt was rate limited.",
    # overloaded(529/미상) 지수 백오프 (초). 마지막 값에서 상한 고정
    "overloadedBackoffSeconds": [30, 60, 120, 300],
    # 같은 pane에 이 간격(초) 안에는 재주입하지 않음 (즉시 재발 루프 방지)
    "minRetryIntervalSeconds": 60,
    # 주입 직전 대상 pane foreground 프로세스가 claude인지 확인할지
    "verifyForegroundProcess": True,
}


class Config(dict):
    """dict 기반 설정. 속성 접근도 허용."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def load(path=None):
    """설정을 로드해 Config 반환. 파일 없음/파싱 실패 시 기본값."""
    cfg = dict(DEFAULTS)
    fpath = path or paths.config_file()
    try:
        with open(fpath, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key, value in data.items():
                if key in DEFAULTS and value is not None:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        # 깨진 설정은 무시하고 기본값으로. (로깅은 호출부에서)
        pass
    return Config(cfg)
