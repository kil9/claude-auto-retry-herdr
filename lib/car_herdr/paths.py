"""런타임 경로 해석 (config / state / logs / markers).

전부 XDG 관례를 따르되 환경변수로 재정의 가능. 저장소 안에 상태를 두지 않는다
(저장소는 코드만, 상태는 사용자 홈).
"""

import os


def _xdg(env_name, default_subpath):
    base = os.environ.get(env_name)
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), default_subpath)


def config_dir():
    # ~/.config/car-herdr
    override = os.environ.get("CAR_HERDR_CONFIG_DIR")
    if override:
        return override
    return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "car-herdr")


def config_file():
    return os.path.join(config_dir(), "config.json")


def state_dir():
    # ~/.local/state/car-herdr — 로그와 마커가 여기 산다
    override = os.environ.get("CAR_HERDR_STATE_DIR")
    if override:
        return override
    return os.path.join(_xdg("XDG_STATE_HOME", os.path.join(".local", "state")), "car-herdr")


def logs_dir():
    return os.path.join(state_dir(), "logs")


def markers_dir():
    # pane별 예약 재시도 마커 (waiter/복구용)
    return os.path.join(state_dir(), "markers")


def ensure_dirs():
    for d in (config_dir(), state_dir(), logs_dir(), markers_dir()):
        os.makedirs(d, exist_ok=True)
