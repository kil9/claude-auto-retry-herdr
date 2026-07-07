"""간단한 파일 로거. state/logs/YYYY-MM-DD.log 에 한 줄씩 추가.

훅/waiter/CLI 모두 같은 포맷으로 쓴다. 로깅 실패가 본 동작을 막지 않도록 방어적.
"""

import datetime
import os
import sys

from . import paths


def _now():
    return datetime.datetime.now()


def log(message, component="car-herdr", also_stderr=False):
    """로그 한 줄 기록. `[ISO8601] [component] message`."""
    now = _now()
    line = f"[{now.isoformat(timespec='seconds')}] [{component}] {message}"
    try:
        os.makedirs(paths.logs_dir(), exist_ok=True)
        fpath = os.path.join(paths.logs_dir(), now.strftime("%Y-%m-%d.log"))
        with open(fpath, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        also_stderr = True
    if also_stderr:
        print(line, file=sys.stderr)
