"""herdr pane 주입 래퍼 + 안전장치.

T4에서 구현. herdr CLI를 subprocess로 호출한다.
  - pane read/send-text/send-keys/run/process-info
  - 주입 전 foreground 프로세스가 claude인지 확인
  - pane이 사라졌으면 조용히 포기
"""


def pane_exists(pane_id):
    """대상 pane이 아직 존재하는지."""
    raise NotImplementedError("T4")


def foreground_is_claude(pane_id):
    """대상 pane의 foreground 프로세스가 claude인지 (오주입 방지)."""
    raise NotImplementedError("T4")


def inject_message(pane_id, message):
    """pane에 텍스트 주입 후 Enter로 제출. 성공 여부 반환."""
    raise NotImplementedError("T4")
