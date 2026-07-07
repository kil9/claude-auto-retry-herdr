"""watch 데몬: 훅이 못 잡은 레이트리밋을 주기적으로 재확인 + 마커 복구.

훅(Stop)이 어떤 이유로든 발화하지 않아도, `herdr agent list`로 claude pane을
열거하고 각 세션의 transcript를 직접 스캔해 레이트리밋을 잡는다. 화면 텍스트
스크레이프보다 견고하다(같은 JSONL 신호 사용).

또한 재시작/크래시로 고아가 된 마커(대기 프로세스 죽음)의 waiter를 되살린다.
서버당 하나 띄우면 모든 pane을 커버한다.
"""

import glob
import json
import os
import time

from . import config, detect, inject, log, waiter

_COMPONENT = "watch"


def _projects_roots():
    """transcript가 있을 수 있는 projects 디렉터리들(realpath 중복 제거)."""
    home = os.path.expanduser("~")
    candidates = [os.path.join(home, ".claude", "projects")]
    candidates += glob.glob(os.path.join(home, ".ccs", "instances", "*", "projects"))
    cd = os.environ.get("CLAUDE_CONFIG_DIR")
    if cd:
        candidates.append(os.path.join(cd, "projects"))
    seen, roots = set(), []
    for c in candidates:
        rp = os.path.realpath(c)
        if rp not in seen and os.path.isdir(rp):
            seen.add(rp)
            roots.append(rp)
    return roots


def resolve_transcript(session_id):
    """session_id로 메인 세션 transcript 경로를 찾는다. 없으면 None.

    경로는 projects/<encoded-cwd>/<session_id>.jsonl. subagents/ 하위는 제외.
    """
    if not session_id:
        return None
    best = None
    for root in _projects_roots():
        for path in glob.glob(os.path.join(root, "*", session_id + ".jsonl")):
            if os.sep + "subagents" + os.sep in path:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, path)
    return best[1] if best else None


def list_claude_panes():
    """herdr agent list에서 claude pane 목록: [(pane_id, session_id)]."""
    obj = inject._run_json(["agent", "list"])
    if not obj:
        return []
    out = []
    for agent in obj.get("result", {}).get("agents", []) or []:
        if agent.get("agent") != "claude":
            continue
        pane_id = agent.get("pane_id")
        sess = (agent.get("agent_session") or {}).get("value")
        if pane_id:
            out.append((pane_id, sess))
    return out


def recover_orphans():
    """대기 프로세스가 죽은 마커의 waiter를 되살린다. 되살린 수 반환."""
    revived = 0
    for marker in waiter.list_markers():
        pane_id = marker.get("pane_id")
        if not pane_id:
            continue
        if waiter._pid_alive(marker.get("waiter_pid")):
            continue
        pid = waiter.spawn_waiter(pane_id)
        if pid:
            marker["waiter_pid"] = pid
            waiter.write_marker(pane_id, marker)
            revived += 1
            log.log(f"고아 마커 waiter 복구 pane={pane_id} pid={pid}", component=_COMPONENT)
    return revived


def poll_once(cfg=None):
    """1회 폴링: 고아 복구 + claude pane별 transcript 재확인. 처리 요약 반환."""
    if cfg is None:
        cfg = config.load()
    revived = recover_orphans()
    scheduled = 0
    for pane_id, session_id in list_claude_panes():
        if waiter.read_marker(pane_id):
            continue  # 이미 예약/처리 중
        tp = resolve_transcript(session_id)
        if not tp:
            continue
        det = detect.scan_transcript_tail(tp)
        if det is None or det.is_subagent or not det.retryable:
            continue
        msg = waiter.schedule_retry(pane_id, tp, session_id, det, cfg=cfg)
        scheduled += 1
        log.log(f"watch: {det.error} 감지(폴백) → {msg} pane={pane_id}", component=_COMPONENT)
    return {"revived": revived, "scheduled": scheduled}


def run_watch(cfg=None, once=False):
    if cfg is None:
        cfg = config.load()
    interval = max(2.0, float(cfg.watchIntervalSeconds))
    log.log(f"watch 시작 interval={interval}s once={once}", component=_COMPONENT)
    while True:
        try:
            poll_once(cfg)
        except Exception as exc:  # 데몬은 죽지 않는다
            log.log(f"watch: poll 예외 무시 {exc!r}", component=_COMPONENT)
        if once:
            return 0
        time.sleep(interval)
