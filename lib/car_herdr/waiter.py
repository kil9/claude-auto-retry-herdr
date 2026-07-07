"""분리형 대기 프로세스 + 마커 파일 관리.

T5에서 구현. 훅이 감지하면 setsid로 이 모듈을 분리 실행한다. 대기 프로세스는
리셋 시각(+margin)까지 자고, 깨어나면 inject로 재시도 메시지를 넣고 종료한다.

마커 파일(state/markers/<pane_id>.json): pane별 예약 재시도 상태.
  { pane_id, transcript_path, session_id, error, wake_at, retries }
"""


def marker_path(pane_id):
    raise NotImplementedError("T5")


def read_marker(pane_id):
    raise NotImplementedError("T5")


def write_marker(pane_id, data):
    raise NotImplementedError("T5")


def clear_marker(pane_id):
    raise NotImplementedError("T5")


def spawn_waiter(marker):
    """setsid로 분리된 대기 프로세스를 띄운다."""
    raise NotImplementedError("T5")


def run_waiter(pane_id):
    """대기 프로세스 본체: 자고 → 안전장치 확인 → 주입 → 마커 갱신/정리."""
    raise NotImplementedError("T5")
