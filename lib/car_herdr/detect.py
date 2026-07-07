"""레이트리밋 감지: transcript JSONL 꼬리 파싱 + 리셋 시각 파싱.

T3에서 구현. 근거는 docs/detection-notes.md.

감지 신호(실측):
  - transcript JSONL 항목에 `isApiErrorMessage: true`
  - `error`: rate_limit | server_error | authentication_failed | model_not_found | unknown
  - `apiErrorStatus`: 429 | 529 | 401 | 404 | null
  - `message.content[0].text`: "You've hit your session limit · resets 8pm (Asia/Seoul)" 등
"""

# 자동 재시도 대상 error 유형 → 처리 전략
RETRYABLE = {
    "rate_limit": "wait_until_reset",   # 리셋 시각까지 대기
    "server_error": "backoff",          # overloaded, 지수 백오프
    "unknown": "backoff",               # "API Error: Overloaded" 등
}
# 재시도하지 않는 유형 (사용자 조치 필요)
NON_RETRYABLE = {"authentication_failed", "model_not_found"}


def scan_transcript_tail(transcript_path, lines=40):
    """transcript 꼬리에서 마지막 API 에러 항목을 찾아 dict로 반환. 없으면 None.

    T3에서 구현.
    """
    raise NotImplementedError("T3")


def parse_reset_time(text, now=None):
    """"resets 8pm (Asia/Seoul)" 형식에서 다음 리셋 datetime(aware) 파싱.

    T3에서 구현.
    """
    raise NotImplementedError("T3")
