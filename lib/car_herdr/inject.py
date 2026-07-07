"""herdr pane 주입 래퍼 + 안전장치.

herdr CLI를 subprocess로 호출한다. 실패는 예외 대신 False/None으로 흡수해
자동 재시도 흐름이 죽지 않게 한다.

안전장치:
  - 주입 전 대상 pane이 존재하는지 확인 (닫혔으면 포기)
  - 주입 전 foreground 프로세스가 claude인지 확인 (사용자가 다른 걸 하면 오주입 방지)
"""

import json
import os
import shutil
import subprocess

HERDR_BIN = os.environ.get("HERDR_BIN", "herdr")
_TIMEOUT = 10


def _run(args, timeout=_TIMEOUT):
    """herdr <args> 실행. (rc, stdout, stderr) 반환. 실행 자체 실패 시 rc=127."""
    binpath = shutil.which(HERDR_BIN) or HERDR_BIN
    try:
        proc = subprocess.run(
            [binpath, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError):
        return 127, "", ""


def _run_json(args, timeout=_TIMEOUT):
    """herdr <args> 실행 후 stdout을 JSON 파싱해 dict 반환. 실패/에러 응답 시 None."""
    rc, out, _ = _run(args, timeout=timeout)
    if rc != 0 or not out.strip():
        return None
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("error"):
        return None
    return obj


def pane_exists(pane_id):
    """대상 pane이 아직 존재하는지."""
    obj = _run_json(["pane", "get", pane_id])
    return bool(obj and obj.get("result", {}).get("pane"))


def foreground_is_claude(pane_id):
    """pane의 foreground 프로세스 중 claude가 있는지 (오주입 방지)."""
    obj = _run_json(["pane", "process-info", "--pane", pane_id])
    if not obj:
        return False
    info = obj.get("result", {}).get("process_info", {})
    for proc in info.get("foreground_processes", []) or []:
        name = (proc.get("name") or "").lower()
        argv = proc.get("argv") or []
        argv0 = os.path.basename(argv[0]).lower() if argv else ""
        if name == "claude" or argv0 == "claude" or argv0.startswith("claude"):
            return True
    return False


def read_recent(pane_id, lines=40):
    """pane 최근 출력 텍스트. 실패 시 ''."""
    rc, out, _ = _run(["pane", "read", pane_id, "--source", "recent", "--lines", str(lines)])
    return out if rc == 0 else ""


def send_text(pane_id, text):
    rc, _, _ = _run(["pane", "send-text", pane_id, text])
    return rc == 0


def send_enter(pane_id):
    rc, _, _ = _run(["pane", "send-keys", pane_id, "Enter"])
    return rc == 0


def inject_message(pane_id, message, verify_foreground=True):
    """pane에 텍스트 주입 후 Enter로 제출.

    반환: (ok: bool, reason: str). reason은 실패 사유(로깅용).
    """
    if not pane_exists(pane_id):
        return False, "pane_gone"
    if verify_foreground and not foreground_is_claude(pane_id):
        return False, "foreground_not_claude"
    if not send_text(pane_id, message):
        return False, "send_text_failed"
    if not send_enter(pane_id):
        return False, "send_enter_failed"
    return True, "ok"
