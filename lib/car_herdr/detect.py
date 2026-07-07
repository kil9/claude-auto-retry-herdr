"""레이트리밋 감지: transcript JSONL 꼬리 파싱 + 리셋 시각 파싱.

근거는 docs/detection-notes.md.

감지 신호(실측):
  - transcript JSONL 항목에 `isApiErrorMessage: true`
  - `error`: rate_limit | server_error | authentication_failed | model_not_found | unknown
  - `apiErrorStatus`: 429 | 529 | 401 | 404 | null
  - `message.content[0].text`: "You've hit your session limit · resets 8pm (Asia/Seoul)" 등
"""

import collections
import datetime
import json
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - python < 3.9
    ZoneInfo = None

# 자동 재시도 대상 error 유형 → 처리 전략
RETRYABLE = {
    "rate_limit": "wait_until_reset",   # 리셋 시각까지 대기
    "server_error": "backoff",          # overloaded, 지수 백오프
    "unknown": "backoff",               # "API Error: Overloaded" 등
}
# 재시도하지 않는 유형 (사용자 조치 필요)
NON_RETRYABLE = {"authentication_failed", "model_not_found"}

# "resets 8pm (Asia/Seoul)", "resets 11:10pm (Asia/Seoul)"
_RESET_RE = re.compile(
    r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

Detection = collections.namedtuple(
    "Detection",
    ["error", "status", "text", "is_subagent", "timestamp", "strategy", "retryable"],
)


def _text_of(obj):
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    if isinstance(content, str):
        return content
    return ""


def classify(obj):
    """API 에러 항목 dict → Detection. 에러 항목이 아니면 None."""
    if not obj.get("isApiErrorMessage"):
        return None
    error = obj.get("error")
    status = obj.get("apiErrorStatus")
    strategy = RETRYABLE.get(error)
    # error 필드가 애매하면(None/미상) status로 보정: 529는 overloaded 백오프
    if strategy is None and error not in NON_RETRYABLE:
        if status == 529:
            strategy = "backoff"
    is_subagent = bool(obj.get("isSidechain") or obj.get("agentId"))
    return Detection(
        error=error,
        status=status,
        text=_text_of(obj),
        is_subagent=is_subagent,
        timestamp=obj.get("timestamp"),
        strategy=strategy,
        retryable=strategy is not None,
    )


def _tail_lines(path, lines):
    # 큰 파일도 안전하게: 끝에서 블록 단위로 역방향 읽기
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        block = 8192
        data = b""
        pos = size
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(block, pos)
            pos -= step
            handle.seek(pos)
            data = handle.read(step) + data
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def scan_transcript_tail(transcript_path, lines=40):
    """transcript 꼬리를 훑어, 마지막 턴이 API 에러로 끝났으면 Detection 반환.

    끝에서부터 스캔하며 첫 assistant 항목을 본다:
      - 그게 API 에러면 Detection 반환(세션이 에러로 멈춘 상태).
      - 정상 assistant면 None(정상 완료).
    user 항목을 먼저 만나면 None(이미 다음 턴 진행 → 에러 만료).
    """
    try:
        raw = _tail_lines(transcript_path, lines)
    except OSError:
        return None
    for line in reversed(raw):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = obj.get("type")
        if etype == "user":
            return None
        if etype == "assistant":
            det = classify(obj)
            return det  # 에러면 Detection, 아니면 None(정상 완료)
        # system / summary / 기타 항목은 건너뛴다
    return None


def parse_reset_time(text, now=None):
    """"resets 8pm (Asia/Seoul)" → 다음 리셋 시각(aware datetime). 실패 시 None."""
    if not text:
        return None
    match = _RESET_RE.search(text)
    if not match or ZoneInfo is None:
        return None
    hour = int(match.group(1)) % 12
    minute = int(match.group(2)) if match.group(2) else 0
    if match.group(3).lower() == "pm":
        hour += 12
    tzname = match.group(4).strip()
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        return None
    if now is None:
        now = datetime.datetime.now(tz)
    else:
        now = now.astimezone(tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


def seconds_until(when, now=None):
    """aware datetime까지 남은 초(음수면 0). now 미지정 시 해당 tz 현재."""
    if when is None:
        return None
    if now is None:
        now = datetime.datetime.now(when.tzinfo)
    delta = (when - now).total_seconds()
    return max(0.0, delta)
