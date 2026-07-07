"""watch 모듈 단위 테스트 (transcript 해석 + 폴백 감지 + 고아 복구).

herdr/실주입을 타지 않도록 list_claude_panes/schedule_retry/spawn_waiter를 스텁.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

_TMP = tempfile.mkdtemp(prefix="car-herdr-watch-")
os.environ["CAR_HERDR_STATE_DIR"] = _TMP
os.environ["CAR_HERDR_CONFIG_DIR"] = _TMP

from car_herdr import config, watch, waiter  # noqa: E402


def write_errored_transcript():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n")
        handle.write(json.dumps({
            "type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
            "apiErrorStatus": 429,
            "message": {"model": "<synthetic>", "role": "assistant",
                        "content": [{"type": "text", "text": "resets 8pm (Asia/Seoul)"}]},
        }) + "\n")
    return path


class PollOnceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self._orig = {
            "list": watch.list_claude_panes,
            "resolve": watch.resolve_transcript,
            "sched": waiter.schedule_retry,
            "spawn": waiter.spawn_waiter,
        }
        self.scheduled = []
        waiter.schedule_retry = lambda p, tp, s, d, cfg=None, now=None: (
            self.scheduled.append((p, d.error)) or "stub")
        waiter.spawn_waiter = lambda p: 424242

    def tearDown(self):
        watch.list_claude_panes = self._orig["list"]
        watch.resolve_transcript = self._orig["resolve"]
        waiter.schedule_retry = self._orig["sched"]
        waiter.spawn_waiter = self._orig["spawn"]
        for m in waiter.list_markers():
            waiter.clear_marker(m.get("pane_id", ""))

    def test_schedules_on_errored_transcript(self):
        tp = write_errored_transcript()
        try:
            watch.list_claude_panes = lambda: [("wZ:p1", "sess-err")]
            watch.resolve_transcript = lambda s: tp
            summary = watch.poll_once(self.cfg)
            self.assertEqual(summary["scheduled"], 1)
            self.assertEqual(self.scheduled, [("wZ:p1", "rate_limit")])
        finally:
            os.remove(tp)

    def test_skips_when_marker_exists(self):
        tp = write_errored_transcript()
        try:
            waiter.write_marker("wZ:p1", {"pane_id": "wZ:p1", "waiter_pid": 424242})
            watch.list_claude_panes = lambda: [("wZ:p1", "sess-err")]
            watch.resolve_transcript = lambda s: tp
            summary = watch.poll_once(self.cfg)
            self.assertEqual(summary["scheduled"], 0)
        finally:
            os.remove(tp)
            waiter.clear_marker("wZ:p1")

    def test_skips_when_no_transcript(self):
        watch.list_claude_panes = lambda: [("wZ:p2", "no-such")]
        watch.resolve_transcript = lambda s: None
        self.assertEqual(watch.poll_once(self.cfg)["scheduled"], 0)

    def test_recover_orphan_respawns(self):
        # 죽은 pid의 마커 → 복구 대상
        waiter.write_marker("wZ:p9", {"pane_id": "wZ:p9", "waiter_pid": 999999999})
        watch.list_claude_panes = lambda: []
        watch.resolve_transcript = lambda s: None
        summary = watch.poll_once(self.cfg)
        self.assertEqual(summary["revived"], 1)
        self.assertEqual(waiter.read_marker("wZ:p9")["waiter_pid"], 424242)
        waiter.clear_marker("wZ:p9")

    def test_no_recover_when_pid_alive(self):
        waiter.write_marker("wZ:p8", {"pane_id": "wZ:p8", "waiter_pid": os.getpid()})
        watch.list_claude_panes = lambda: []
        summary = watch.poll_once(self.cfg)
        self.assertEqual(summary["revived"], 0)
        waiter.clear_marker("wZ:p8")


if __name__ == "__main__":
    unittest.main()
