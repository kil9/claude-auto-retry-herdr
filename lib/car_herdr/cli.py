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

    sub.add_parser("install", help="Claude Code 훅 등록 (T6)").set_defaults(func=_todo("install", "T6"))
    sub.add_parser("uninstall", help="훅 제거 (T6)").set_defaults(func=_todo("uninstall", "T6"))
    sub.add_parser("retry-now", help="즉시 재시도 (T5)").set_defaults(func=_todo("retry-now", "T5"))
    sub.add_parser("hook", help="(내부) 훅 진입점 (T6)").set_defaults(func=_todo("hook", "T6"))

    p_waiter = sub.add_parser("run-waiter", help="(내부) 대기 프로세스 본체 (T5)")
    p_waiter.add_argument("pane_id")
    p_waiter.set_defaults(func=_todo("run-waiter", "T5"))

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
