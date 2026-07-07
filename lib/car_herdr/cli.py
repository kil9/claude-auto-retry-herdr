"""car-herdr CLI. 진입점(bin/car-herdr)이 이 main을 호출한다.

서브커맨드:
  install     Claude Code 훅 등록 (T6)
  uninstall   훅 제거 (T6)
  status      설정/상태/예약 재시도 마커 출력
  logs        오늘 로그 출력 (--date 로 특정 날짜)
  retry-now   예약 대기를 무시하고 즉시 재시도 (T5)
  hook        (내부) 훅 진입점 — transcript 검사 후 waiter spawn (T6)
  run-waiter  (내부) 분리형 대기 프로세스 본체 (T5)
"""

import argparse
import json
import os
import sys

from . import __version__, config, paths


def _print_kv(key, value):
    print(f"  {key:24} {value}")


def cmd_status(args):
    cfg = config.load()
    print(f"car-herdr {__version__}")
    print("경로:")
    _print_kv("config", paths.config_file())
    _print_kv("state", paths.state_dir())
    _print_kv("logs", paths.logs_dir())
    _print_kv("markers", paths.markers_dir())
    print("설정 (기본값 병합 후):")
    for key in sorted(cfg):
        _print_kv(key, json.dumps(cfg[key], ensure_ascii=False))
    print("예약 재시도 마커:")
    mdir = paths.markers_dir()
    markers = []
    if os.path.isdir(mdir):
        markers = [f for f in os.listdir(mdir) if f.endswith(".json")]
    if not markers:
        print("  (없음)")
    else:
        for name in sorted(markers):
            try:
                with open(os.path.join(mdir, name), encoding="utf-8") as handle:
                    data = json.load(handle)
                _print_kv(name, json.dumps(data, ensure_ascii=False))
            except (OSError, json.JSONDecodeError):
                _print_kv(name, "(읽기 실패)")
    return 0


def cmd_logs(args):
    import datetime

    date = args.date or datetime.date.today().strftime("%Y-%m-%d")
    fpath = os.path.join(paths.logs_dir(), f"{date}.log")
    try:
        with open(fpath, encoding="utf-8") as handle:
            sys.stdout.write(handle.read())
    except FileNotFoundError:
        print(f"로그 없음: {fpath}", file=sys.stderr)
        return 1
    return 0


def cmd_hook(args):
    """(내부) Claude Code Stop 훅 진입점. stdin으로 훅 JSON을 받는다.

    무슨 일이 있어도 0으로 종료해 Claude 세션을 막지 않는다.
    """
    from . import detect, log, waiter

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    try:
        if data.get("agent_id"):  # 서브에이전트 이벤트는 무시(메인 세션만)
            return 0
        transcript = data.get("transcript_path")
        session = data.get("session_id")
        pane = os.environ.get("HERDR_PANE_ID")
        if not transcript or not pane:
            return 0
        det = detect.scan_transcript_tail(transcript)
        if det is None or det.is_subagent:
            return 0
        if not det.retryable:
            log.log(f"hook: 재시도 불가 에러({det.error}) 감지, 건너뜀 pane={pane}", component="hook")
            return 0
        msg = waiter.schedule_retry(pane, transcript, session, det)
        log.log(f"hook: {det.error} 감지 → {msg} pane={pane}", component="hook")
    except Exception as exc:  # 훅은 절대 세션을 막으면 안 됨
        log.log(f"hook: 예외 무시 {exc!r}", component="hook")
    return 0


def _settings_targets(args):
    from . import install

    if getattr(args, "all_instances", False):
        import glob

        base = os.path.join(os.path.expanduser("~"), ".ccs", "instances")
        paths = [install.resolve_settings_path(d) for d in glob.glob(os.path.join(base, "*"))]
    elif getattr(args, "config_dir", None):
        paths = [install.resolve_settings_path(d) for d in args.config_dir]
    else:
        paths = [install.resolve_settings_path()]
    # realpath 기준 중복 제거(심볼릭 shared 공유 대비)
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def cmd_install(args):
    from . import install

    rc = 0
    print(f"훅 스크립트: {install.HOOK_SCRIPT}")
    for path in _settings_targets(args):
        status, message = install.install(path)
        print(f"  [{status}] {message}")
        if status == "error":
            rc = 1
    return rc


def cmd_uninstall(args):
    from . import install

    rc = 0
    for path in _settings_targets(args):
        status, message = install.uninstall(path)
        print(f"  [{status}] {message}")
        if status == "error":
            rc = 1
    return rc


def cmd_run_waiter(args):
    from . import waiter

    return waiter.run_waiter(args.pane_id)


def cmd_retry_now(args):
    import time

    from . import waiter

    pane_id = args.pane or os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        print("대상 pane을 알 수 없습니다 (--pane 또는 $HERDR_PANE_ID 필요).", file=sys.stderr)
        return 2
    marker = waiter.read_marker(pane_id)
    if not marker:
        print(f"예약된 재시도가 없습니다: {pane_id}", file=sys.stderr)
        return 1
    marker["wake_at"] = time.time()
    waiter.write_marker(pane_id, marker)
    if not waiter._pid_alive(marker.get("waiter_pid")):
        pid = waiter.spawn_waiter(pane_id)
        if pid:
            marker["waiter_pid"] = pid
            waiter.write_marker(pane_id, marker)
    print(f"즉시 재시도 예약: {pane_id}")
    return 0


def _todo(name, task):
    def handler(args):
        print(f"'{name}'는 아직 구현 전입니다 ({task}).", file=sys.stderr)
        return 2

    return handler


def build_parser():
    parser = argparse.ArgumentParser(prog="car-herdr", description="herdr 네이티브 Claude Code 레이트리밋 자동 재시도")
    parser.add_argument("--version", action="version", version=f"car-herdr {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="설정/상태/예약 재시도 마커 출력").set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="로그 출력")
    p_logs.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    p_logs.set_defaults(func=cmd_logs)

    def add_target_opts(p):
        p.add_argument("--config-dir", action="append", metavar="DIR",
                       help="대상 CLAUDE_CONFIG_DIR (반복 가능, 기본: $CLAUDE_CONFIG_DIR)")
        p.add_argument("--all-instances", action="store_true",
                       help="~/.ccs/instances/* 전체에 적용")

    p_install = sub.add_parser("install", help="Claude Code Stop 훅 등록")
    add_target_opts(p_install)
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="훅 제거")
    add_target_opts(p_uninstall)
    p_uninstall.set_defaults(func=cmd_uninstall)

    sub.add_parser("hook", help="(내부) Stop 훅 진입점").set_defaults(func=cmd_hook)

    p_retry = sub.add_parser("retry-now", help="예약 대기를 무시하고 즉시 재시도")
    p_retry.add_argument("--pane", help="대상 pane id (기본: $HERDR_PANE_ID)")
    p_retry.set_defaults(func=cmd_retry_now)

    p_waiter = sub.add_parser("run-waiter", help="(내부) 대기 프로세스 본체")
    p_waiter.add_argument("pane_id")
    p_waiter.set_defaults(func=cmd_run_waiter)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
