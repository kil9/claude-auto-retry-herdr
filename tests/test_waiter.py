"""waiter 모듈 단위 테스트 (마커 CRUD + 스케줄 정책). stdlib unittest.

상태 디렉터리를 임시 경로로 격리(CAR_HERDR_STATE_DIR)한 뒤 모듈을 import한다.
"""

import datetime
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

_TMP = tempfile.mkdtemp(prefix="car-herdr-test-")
os.environ["CAR_HERDR_STATE_DIR"] = _TMP
os.environ["CAR_HERDR_CONFIG_DIR"] = _TMP  # 설정 파일 없음 → 기본값

from car_herdr import config, detect, status, waiter  # noqa: E402

# 단위 테스트는 herdr를 호출하지 않는다: 상태바 표시를 no-op으로.
status.set_scheduled = lambda *a, **k: None
status.clear = lambda *a, **k: None


def det(error, strategy, text=""):
    return detect.Detection(error=error, status=None, text=text,
                            is_subagent=False, timestamp=None,
                            strategy=strategy, retryable=strategy is not None)


class MarkerCrudTest(unittest.TestCase):
    def setUp(self):
        self.pane = "wX:p9"
        waiter.clear_marker(self.pane)

    def test_roundtrip(self):
        self.assertIsNone(waiter.read_marker(self.pane))
        waiter.write_marker(self.pane, {"pane_id": self.pane, "retries": 2})
        got = waiter.read_marker(self.pane)
        self.assertEqual(got["retries"], 2)

    def test_clear(self):
        waiter.write_marker(self.pane, {"pane_id": self.pane})
        waiter.clear_marker(self.pane)
        self.assertIsNone(waiter.read_marker(self.pane))
        waiter.clear_marker(self.pane)  # 없는 것 지워도 예외 없음

    def test_safe_name_colon(self):
        self.assertNotIn(":", os.path.basename(waiter.marker_path("wX:p9")))

    def test_list_markers(self):
        waiter.clear_marker(self.pane)
        waiter.write_marker(self.pane, {"pane_id": self.pane})
        panes = [m.get("pane_id") for m in waiter.list_markers()]
        self.assertIn(self.pane, panes)


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_backoff_sequence(self):
        self.assertEqual(waiter.backoff_seconds(0, self.cfg), 30.0)
        self.assertEqual(waiter.backoff_seconds(1, self.cfg), 60.0)
        self.assertEqual(waiter.backoff_seconds(3, self.cfg), 300.0)

    def test_backoff_caps_at_last(self):
        self.assertEqual(waiter.backoff_seconds(99, self.cfg), 300.0)


class ComputeWakeTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_backoff_strategy(self):
        now = 1000.0
        got = waiter.compute_wake_at("backoff", "", 1, self.cfg, now=now)
        self.assertEqual(got, now + 60.0)

    def test_reset_strategy_uses_margin(self):
        tz = ZoneInfo("Asia/Seoul")
        # 리셋 시각을 미래로 두고, 파싱된 reset + margin인지 확인
        now_dt = datetime.datetime.now(tz)
        text = f"resets {((now_dt.hour + 2) % 12) or 12}pm (Asia/Seoul)"
        got = waiter.compute_wake_at("wait_until_reset", "resets 8pm (Asia/Seoul)", 0, self.cfg)
        reset = detect.parse_reset_time("resets 8pm (Asia/Seoul)")
        self.assertAlmostEqual(got, reset.timestamp() + self.cfg.marginSeconds, places=3)

    def test_reset_parse_failure_fallback(self):
        now = 5000.0
        got = waiter.compute_wake_at("wait_until_reset", "no reset here", 0, self.cfg, now=now)
        self.assertEqual(got, now + self.cfg.fallbackWaitHours * 3600.0)


class ScheduleRetryTest(unittest.TestCase):
    def setUp(self):
        self.pane = "wX:p1"
        self.cfg = config.load()
        waiter.clear_marker(self.pane)
        # 실제 대기 프로세스 spawn 방지(테스트 레이스 차단)
        self._orig_spawn = waiter.spawn_waiter
        waiter.spawn_waiter = lambda pane_id: 999999

    def tearDown(self):
        waiter.spawn_waiter = self._orig_spawn
        waiter.clear_marker(self.pane)

    def test_non_retryable_skipped(self):
        d = det("authentication_failed", None)
        msg = waiter.schedule_retry(self.pane, "/t.jsonl", "s1", d, cfg=self.cfg, now=1000.0)
        self.assertIn("non-retryable", msg)
        self.assertIsNone(waiter.read_marker(self.pane))

    def test_first_schedule_writes_marker(self):
        d = det("server_error", "backoff")
        msg = waiter.schedule_retry(self.pane, "/t.jsonl", "s1", d, cfg=self.cfg, now=1000.0)
        self.assertIn("scheduled", msg)
        m = waiter.read_marker(self.pane)
        self.assertEqual(m["retries"], 0)
        self.assertEqual(m["strategy"], "backoff")
        self.assertEqual(m["wake_at"], 1000.0 + 30.0)

    def test_cap_reached_within_interval(self):
        d = det("server_error", "backoff")
        waiter.write_marker(self.pane, {
            "pane_id": self.pane, "retries": self.cfg.maxRetries,
            "last_error_at": 1000.0, "created_at": 1000.0,
        })
        msg = waiter.schedule_retry(self.pane, "/t.jsonl", "s1", d, cfg=self.cfg, now=1005.0)
        self.assertIn("cap reached", msg)

    def test_streak_resets_after_interval(self):
        d = det("server_error", "backoff")
        waiter.write_marker(self.pane, {
            "pane_id": self.pane, "retries": self.cfg.maxRetries,
            "last_error_at": 1000.0, "created_at": 1000.0,
        })
        # 최소 간격을 훨씬 넘긴 뒤 재발 → streak 리셋되어 다시 스케줄
        later = 1000.0 + self.cfg.minRetryIntervalSeconds + 10
        msg = waiter.schedule_retry(self.pane, "/t.jsonl", "s1", d, cfg=self.cfg, now=later)
        self.assertIn("scheduled", msg)
        self.assertEqual(waiter.read_marker(self.pane)["retries"], 0)


if __name__ == "__main__":
    unittest.main()
