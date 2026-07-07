"""분리형 대기 프로세스 + 마커 파일 관리.

훅(T6)이 레이트리밋을 감지하면 schedule_retry로 마커를 쓰고 setsid 분리
대기 프로세스를 띄운다. 대기 프로세스는 wake_at까지 자고, 깨어나면 안전장치를
확인한 뒤 재시도 메시지를 주입하고 종료한다.

마커 파일(state/markers/<pane>.json):
  { pane_id, transcript_path, session_id, error, strategy,
    wake_at(epoch), retries, created_at, updated_at, last_error_at, waiter_pid }
"""

import json
import os
import signal
import subprocess
import sys
import time

from . import config, detect, inject, log, paths, status

_COMPONENT = "waiter"
_REPO_BIN = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "bin", "car-herdr")
)


# ---- 마커 CRUD ---------------------------------------------------------------

def _safe_name(pane_id):
    return pane_id.replace("/", "_").replace(":", "_")


def marker_path(pane_id):
    return os.path.join(paths.markers_dir(), _safe_name(pane_id) + ".json")


def read_marker(pane_id):
    try:
        with open(marker_path(pane_id), encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    except OSError:
        return None


def write_marker(pane_id, data):
    paths.ensure_dirs()
    fpath = marker_path(pane_id)
    tmp = fpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, fpath)


def clear_marker(pane_id):
    try:
        os.remove(marker_path(pane_id))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def list_markers():
    mdir = paths.markers_dir()
    result = []
    if not os.path.isdir(mdir):
        return result
    for name in sorted(os.listdir(mdir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(mdir, name), encoding="utf-8") as handle:
                result.append(json.load(handle))
        except (OSError, json.JSONDecodeError):
            pass
    return result


# ---- 스케줄 계산 -------------------------------------------------------------

def backoff_seconds(retries, cfg):
    seq = cfg.overloadedBackoffSeconds or [30]
    idx = min(max(retries, 0), len(seq) - 1)
    return float(seq[idx])


def compute_wake_at(strategy, text, retries, cfg, now=None):
    """전략별로 다음 시도 시각(epoch)을 계산."""
    if now is None:
        now = time.time()
    if strategy == "wait_until_reset":
        reset = detect.parse_reset_time(text)
        if reset is not None:
            return reset.timestamp() + cfg.marginSeconds
        return now + cfg.fallbackWaitHours * 3600.0
    # backoff (529/overloaded)
    return now + backoff_seconds(retries, cfg)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


# ---- 훅이 호출하는 진입점 ----------------------------------------------------

def schedule_retry(pane_id, transcript_path, session_id, detection, cfg=None, now=None):
    """레이트리밋 감지 시 마커를 쓰고 대기 프로세스를 확보한다.

    detection: detect.Detection. cap/최소간격 정책을 여기서 강제한다.
    반환: 동작 설명 문자열(로깅용).
    """
    if cfg is None:
        cfg = config.load()
    if now is None:
        now = time.time()
    if not detection.retryable:
        return f"skip: non-retryable ({detection.error})"

    marker = read_marker(pane_id)
    retries = 0
    if marker:
        # 살아있는 대기 프로세스가 아직 예약 시각 전이면 그대로 둔다.
        # (훅 반복 발화 / watch 폴링이 wake_at을 계속 밀어내는 것을 방지)
        if _pid_alive(marker.get("waiter_pid")) and now < marker.get("wake_at", 0):
            delay = int(marker["wake_at"] - now)
            return f"already pending: wake_in={delay}s retries={marker.get('retries', 0)}"
        last = marker.get("last_error_at", 0)
        # 최근 시도가 최소 간격 안이면 streak로 이어 cap을 적용, 아니면 리셋
        if now - last < max(cfg.minRetryIntervalSeconds, 0):
            retries = marker.get("retries", 0)
            if retries >= cfg.maxRetries:
                return f"skip: cap reached ({retries}/{cfg.maxRetries})"
        else:
            retries = 0

    wake_at = compute_wake_at(detection.strategy, detection.text, retries, cfg, now=now)
    data = {
        "pane_id": pane_id,
        "transcript_path": transcript_path,
        "session_id": session_id,
        "error": detection.error,
        "strategy": detection.strategy,
        "wake_at": wake_at,
        "retries": retries,
        "created_at": (marker or {}).get("created_at", now),
        "updated_at": now,
        "last_error_at": now,
        "waiter_pid": (marker or {}).get("waiter_pid"),
    }
    write_marker(pane_id, data)

    if not _pid_alive(data.get("waiter_pid")):
        pid = spawn_waiter(pane_id)
        if pid:
            data["waiter_pid"] = pid
            write_marker(pane_id, data)
    status.set_scheduled(pane_id, wake_at, detection.strategy, now=now)
    delay = max(0, int(wake_at - now))
    return f"scheduled: strategy={detection.strategy} wake_in={delay}s retries={retries}"


def spawn_waiter(pane_id):
    """setsid(분리 세션) 대기 프로세스를 띄운다. 실패 시 None."""
    try:
        devnull = open(os.devnull, "r+b")
        proc = subprocess.Popen(
            [sys.executable, _REPO_BIN, "run-waiter", pane_id],
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,  # setsid 상당
            close_fds=True,
        )
        return proc.pid
    except (OSError, subprocess.SubprocessError) as exc:
        log.log(f"spawn_waiter 실패: {exc}", component=_COMPONENT)
        return None


# ---- 대기 프로세스 본체 ------------------------------------------------------

def _finish(pane_id):
    """대기 종료 공통 정리: 상태바 + 마커 제거."""
    status.clear(pane_id)
    clear_marker(pane_id)


def run_waiter(pane_id):
    """자고 → 안전장치 확인 → 주입 → 마커 정리. 종료 조건마다 로그."""
    cfg = config.load()
    poll = max(1.0, float(cfg.pollIntervalSeconds))
    log.log(f"waiter 시작 pane={pane_id}", component=_COMPONENT)
    while True:
        marker = read_marker(pane_id)
        if not marker:
            log.log(f"marker 사라짐, 종료 pane={pane_id}", component=_COMPONENT)
            status.clear(pane_id)
            return 0
        now = time.time()
        wake = marker.get("wake_at", now)
        if now < wake:
            time.sleep(min(poll, wake - now))
            continue

        if marker.get("retries", 0) >= cfg.maxRetries:
            log.log(f"cap 도달, 포기 pane={pane_id}", component=_COMPONENT)
            _finish(pane_id)
            return 0
        if not inject.pane_exists(pane_id):
            log.log(f"pane 사라짐, 포기 pane={pane_id}", component=_COMPONENT)
            _finish(pane_id)
            return 0
        det = detect.scan_transcript_tail(marker.get("transcript_path", ""))
        if det is None:
            log.log(f"이미 해소됨(정상 진행), 종료 pane={pane_id}", component=_COMPONENT)
            _finish(pane_id)
            return 0
        if cfg.verifyForegroundProcess and not inject.foreground_is_claude(pane_id):
            log.log(f"foreground가 claude 아님, 포기 pane={pane_id}", component=_COMPONENT)
            _finish(pane_id)
            return 0

        ok, reason = inject.inject_message(
            pane_id, cfg.retryMessage, verify_foreground=cfg.verifyForegroundProcess
        )
        marker["retries"] = marker.get("retries", 0) + 1
        marker["updated_at"] = now
        marker["last_inject_reason"] = reason
        if ok:
            log.log(
                f"재시도 주입 성공 pane={pane_id} retries={marker['retries']}",
                component=_COMPONENT,
            )
            _finish(pane_id)
            return 0
        # 주입 실패(일시적): 짧은 백오프 후 cap 내 재시도
        marker["wake_at"] = now + backoff_seconds(marker["retries"], cfg)
        marker["waiter_pid"] = os.getpid()
        write_marker(pane_id, marker)
        log.log(f"주입 실패({reason}), 백오프 재시도 pane={pane_id}", component=_COMPONENT)
