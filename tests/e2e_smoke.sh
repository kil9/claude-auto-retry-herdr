#!/usr/bin/env bash
# car-herdr E2E 스모크 (수동). herdr 안에서 실행해야 한다(HERDR_ENV=1).
#
# 전체 체인을 실제 herdr pane에서 검증한다:
#   훅(Stop replay) → transcript 감지 → schedule_retry → 분리형 waiter →
#   foreground=claude 안전장치 → 실제 pane 주입 → 마커 정리.
#
# 실제 레이트리밋을 기다릴 수 없으므로, 에러 상태 transcript를 심고 Stop 훅을
# 수동 발화(replay)한다. 대상 pane은 argv0를 'claude'로 위장한 인터랙티브 bash라
# 주입된 명령이 실제로 실행되어 눈으로 확인된다.
set -u

[ "${HERDR_ENV:-}" = "1" ] || { echo "herdr 안에서 실행하세요 (HERDR_ENV=1)"; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/car-e2e.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/cfg"
cat > "$WORK/cfg/config.json" <<'JSON'
{"overloadedBackoffSeconds":[2], "retryMessage":"echo CAR_E2E_INJECTED_OK", "pollIntervalSeconds":1}
JSON
cat > "$WORK/err.jsonl" <<'JSON'
{"type":"user","message":{"role":"user","content":"go"}}
{"type":"assistant","isApiErrorMessage":true,"error":"server_error","apiErrorStatus":529,"message":{"model":"<synthetic>","role":"assistant","content":[{"type":"text","text":"API Error: 529 Overloaded."}]}}
JSON

PANE=$(herdr tab create --no-focus --label car-e2e 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['root_pane']['pane_id'])")
echo "scratch pane: $PANE"
cleanup_tab() {
  for tid in $(herdr tab list 2>/dev/null | python3 -c "import sys,json;print(' '.join(t['tab_id'] for t in json.load(sys.stdin).get('result',{}).get('tabs',[]) if 'car-e2e'==(t.get('label') or '')))"); do
    herdr tab close "$tid" >/dev/null 2>&1
  done
}
trap 'cleanup_tab; rm -rf "$WORK"' EXIT

herdr pane run "$PANE" 'exec -a claude bash --norc -i' >/dev/null 2>&1
sleep 1

echo '{"hook_event_name":"Stop","transcript_path":"'"$WORK/err.jsonl"'","session_id":"e2e"}' | \
  HERDR_ENV=1 HERDR_PANE_ID="$PANE" CAR_HERDR_STATE_DIR="$WORK/st" CAR_HERDR_CONFIG_DIR="$WORK/cfg" \
  "$REPO/hooks/car-herdr-hook.sh"

sleep 5
HITS=$(herdr pane read "$PANE" --source visible 2>&1 | grep -c CAR_E2E_INJECTED_OK)
echo "주입 확인: 'CAR_E2E_INJECTED_OK' ${HITS}회"
echo "--- 로그 ---"; cat "$WORK/st/logs/"*.log 2>/dev/null

if [ "$HITS" -ge 1 ] && [ ! -d "$WORK/st/markers" -o -z "$(ls -A "$WORK/st/markers" 2>/dev/null)" ]; then
  echo "PASS: 감지→대기→주입→마커정리 전 과정 확인"
  exit 0
fi
echo "FAIL"; exit 1
