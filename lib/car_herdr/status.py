"""herdr pane 상태바 표시 (custom_status via report-metadata).

재시도가 예약되면 대상 pane에 "⏳ 재시도 HH:MM" 같은 커스텀 상태를 얹고, 주입
완료/포기 시 지운다. 실패는 조용히 흡수(표시는 부가 기능).
"""

import datetime

from . import inject

SOURCE = "car-herdr"


def _hhmm(epoch):
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%H:%M")
    except (OSError, OverflowError, ValueError):
        return "?"


def set_scheduled(pane_id, wake_at, strategy, now=None):
    """예약 상태를 pane에 표시. now는 ttl 계산용(초 epoch)."""
    if strategy == "wait_until_reset":
        text = f"⏳ car-herdr 재시도 {_hhmm(wake_at)}"
    else:
        text = f"⏳ car-herdr 재시도 {_hhmm(wake_at)} (백오프)"
    if now is None:
        import time

        now = time.time()
    ttl_ms = int(max(60.0, (wake_at - now) + 3600.0) * 1000)
    inject._run([
        "pane", "report-metadata", pane_id,
        "--source", SOURCE,
        "--custom-status", text,
        "--ttl-ms", str(ttl_ms),
    ])


def clear(pane_id):
    inject._run([
        "pane", "report-metadata", pane_id,
        "--source", SOURCE,
        "--clear-custom-status",
    ])
