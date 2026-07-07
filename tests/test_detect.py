"""detect 모듈 단위 테스트 (stdlib unittest).

실행: python3 -m unittest discover -s tests   (저장소 루트에서)
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from car_herdr import detect  # noqa: E402


def err_entry(error, status, text, is_subagent=False):
    obj = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "error": error,
        "apiErrorStatus": status,
        "timestamp": "2026-07-07T10:42:18.323Z",
        "message": {"model": "<synthetic>", "role": "assistant",
                    "content": [{"type": "text", "text": text}]},
    }
    if is_subagent:
        obj["isSidechain"] = True
        obj["agentId"] = "sub-abc"
    return obj


def assistant_ok(text="done"):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def user_msg(text="next"):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write_jsonl(entries):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


class ClassifyTest(unittest.TestCase):
    def test_rate_limit(self):
        det = detect.classify(err_entry("rate_limit", 429, "You've hit your session limit · resets 8pm (Asia/Seoul)"))
        self.assertEqual(det.strategy, "wait_until_reset")
        self.assertTrue(det.retryable)
        self.assertFalse(det.is_subagent)

    def test_server_error_backoff(self):
        det = detect.classify(err_entry("server_error", 529, "API Error: 529 Overloaded."))
        self.assertEqual(det.strategy, "backoff")

    def test_unknown_overloaded_backoff(self):
        det = detect.classify(err_entry("unknown", None, "API Error: Overloaded"))
        self.assertEqual(det.strategy, "backoff")

    def test_status_529_without_error_field(self):
        det = detect.classify(err_entry(None, 529, "API Error: 529"))
        self.assertEqual(det.strategy, "backoff")

    def test_auth_not_retryable(self):
        det = detect.classify(err_entry("authentication_failed", 401, "Please run /login"))
        self.assertIsNone(det.strategy)
        self.assertFalse(det.retryable)

    def test_model_not_found_not_retryable(self):
        det = detect.classify(err_entry("model_not_found", 404, "issue with the selected model"))
        self.assertFalse(det.retryable)

    def test_subagent_flag(self):
        det = detect.classify(err_entry("rate_limit", 429, "resets 8pm (Asia/Seoul)", is_subagent=True))
        self.assertTrue(det.is_subagent)

    def test_non_error_entry(self):
        self.assertIsNone(detect.classify(assistant_ok()))


class ScanTailTest(unittest.TestCase):
    def test_error_last_turn(self):
        path = write_jsonl([user_msg(), assistant_ok(), err_entry("rate_limit", 429, "resets 8pm (Asia/Seoul)")])
        try:
            det = detect.scan_transcript_tail(path)
            self.assertIsNotNone(det)
            self.assertEqual(det.error, "rate_limit")
        finally:
            os.remove(path)

    def test_normal_completion(self):
        path = write_jsonl([user_msg(), err_entry("rate_limit", 429, "x"), user_msg(), assistant_ok()])
        try:
            self.assertIsNone(detect.scan_transcript_tail(path))
        finally:
            os.remove(path)

    def test_stale_error_then_user(self):
        # 에러 후 사용자가 이미 다음 턴 시작 → 만료로 봐야 함
        path = write_jsonl([err_entry("rate_limit", 429, "x"), user_msg()])
        try:
            self.assertIsNone(detect.scan_transcript_tail(path))
        finally:
            os.remove(path)

    def test_missing_file(self):
        self.assertIsNone(detect.scan_transcript_tail("/no/such/file.jsonl"))


class ParseResetTest(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Seoul")

    def test_pm_future(self):
        now = datetime.datetime(2026, 7, 7, 15, 0, tzinfo=self.tz)
        got = detect.parse_reset_time("resets 8pm (Asia/Seoul)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 7, 20, 0, tzinfo=self.tz))

    def test_with_minutes(self):
        now = datetime.datetime(2026, 7, 7, 20, 0, tzinfo=self.tz)
        got = detect.parse_reset_time("resets 11:10pm (Asia/Seoul)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 7, 23, 10, tzinfo=self.tz))

    def test_rollover_to_next_day(self):
        # 새벽 4:10am인데 지금이 오후 → 다음 날로 롤오버
        now = datetime.datetime(2026, 7, 7, 15, 0, tzinfo=self.tz)
        got = detect.parse_reset_time("resets 4:10am (Asia/Seoul)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 8, 4, 10, tzinfo=self.tz))

    def test_12am_midnight(self):
        now = datetime.datetime(2026, 7, 7, 15, 0, tzinfo=self.tz)
        got = detect.parse_reset_time("resets 12am (Asia/Seoul)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 8, 0, 0, tzinfo=self.tz))

    def test_12pm_noon(self):
        now = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=self.tz)
        got = detect.parse_reset_time("resets 12pm (Asia/Seoul)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 7, 12, 0, tzinfo=self.tz))

    def test_cross_timezone(self):
        # 메시지 TZ가 로컬과 달라도 그 TZ 기준으로 계산
        now = datetime.datetime(2026, 7, 7, 15, 0, tzinfo=self.tz)  # 서울 15시 = UTC 6시
        got = detect.parse_reset_time("resets 8am (UTC)", now=now)
        self.assertEqual(got, datetime.datetime(2026, 7, 7, 8, 0, tzinfo=ZoneInfo("UTC")))

    def test_unparseable(self):
        self.assertIsNone(detect.parse_reset_time("something unrelated"))

    def test_bad_timezone(self):
        self.assertIsNone(detect.parse_reset_time("resets 8pm (Not/AZone)"))

    def test_seconds_until(self):
        now = datetime.datetime(2026, 7, 7, 15, 0, tzinfo=self.tz)
        when = datetime.datetime(2026, 7, 7, 15, 2, tzinfo=self.tz)
        self.assertEqual(detect.seconds_until(when, now=now), 120.0)
        past = datetime.datetime(2026, 7, 7, 14, 0, tzinfo=self.tz)
        self.assertEqual(detect.seconds_until(past, now=now), 0.0)


if __name__ == "__main__":
    unittest.main()
