# 감지 노트 (T1 실측)

herdr 0.7.1-preview 위에서 도는 Claude Code(`CLAUDE_CONFIG_DIR=~/.ccs/instances/*`)를
대상으로, 레이트리밋을 어떻게 감지할지 실측한 결과. 로컬 transcript JSONL 수백 개와
herdr 소스(`~/work/kil9/herdr`)를 근거로 정리했다.

## 결론 요약

- 1차 트리거는 **Claude Code 훅(`Stop`) + transcript 꼬리 파싱**으로 간다. 신호가
  transcript JSONL에 구조화 필드로 남아 오탐이 거의 없다.
- 2차 트리거로 검토했던 **herdr `blocked` 상태는 레이트리밋에 쓸 수 없다.** herdr의
  claude 상태 판정(`src/detect/manifests/claude.toml`)은 인터랙티브 프롬프트(권한/폼/
  워크플로 선택)만 `blocked`로 본다. 레이트리밋/`API Error`는 상태로 노출되지 않는다.
- 3차(pane 스크레이프)는 훅이 이벤트를 못 주는 예외 경로 폴백으로만 남긴다.

## transcript 항목 구조 (핵심 신호)

레이트리밋/에러는 transcript JSONL에 **합성(synthetic) assistant 항목** 한 줄로 남는다.
실측한 한 줄(429 세션 한도):

```json
{
  "type": "assistant",
  "isApiErrorMessage": true,
  "apiErrorStatus": 429,
  "error": "rate_limit",
  "isSidechain": false,
  "agentId": null,
  "message": {
    "model": "<synthetic>",
    "stop_reason": "stop_sequence",
    "role": "assistant",
    "content": [{"type": "text", "text": "You've hit your session limit · resets 8pm (Asia/Seoul)"}]
  },
  "timestamp": "2026-07-07T10:42:18.323Z"
}
```

감지에 쓸 필드:

| 필드 | 값 | 용도 |
| --- | --- | --- |
| `isApiErrorMessage` | `true` | 에러 항목 1차 필터 (이거 하나로 거의 확정) |
| `error` | `rate_limit` / `server_error` / `authentication_failed` / `model_not_found` / `unknown` | 에러 종류 분기 |
| `apiErrorStatus` | `429` / `529` / `401` / `404` / `null` | HTTP 상태 |
| `message.content[0].text` | 사람용 문구 | 리셋 시각 파싱 원문 |
| `message.model` | `<synthetic>` | 합성 항목 확인용 보조 |
| `isSidechain` / `agentId` | 서브에이전트 여부 | 메인 세션만 재시도할지 판단 |

## 에러 taxonomy (로컬 transcript 실측 분포)

| `error` | status | 건수 | 처리 방침 |
| --- | --- | --- | --- |
| `rate_limit` | 429 | 17 | **주 대상.** `resets <시각> (<TZ>)` 파싱 → 리셋+margin까지 대기 후 재시도 |
| `server_error` | 529 | 1 | overloaded. 지수 백오프 재시도 (리셋 시각 없음) |
| `authentication_failed` | 401 | 3 | 사용자 조치 필요(`/login`). **자동 재시도 안 함** |
| `model_not_found` | 404 | 1 | 사용자 조치 필요. 재시도 안 함 |
| `unknown` | null | 1 | 예: `API Error: Overloaded`. overloaded로 간주해 백오프 |

실측 문구 예시:

- `You've hit your session limit · resets 8pm (Asia/Seoul)`
- `You've hit your session limit · resets 11:10pm (Asia/Seoul)`
- `You've hit your session limit · resets 4:10am (Asia/Seoul)`
- `API Error: 529 Overloaded. This is a server-side issue, usually temporary — try again in a moment. If it persists, check https://status.claude.com.`
- `API Error: Overloaded`
- `Please run /login · API Error: 401 Invalid authentication cr...`

## 리셋 시각 파싱

- 형식: `resets <시각> (<IANA TZ>)`.
- 시각은 12시간제 `h[:mm](am|pm)` (`8pm`, `11:10pm`, `8:50pm`, `4:10am`).
- **타임존은 PLAN이 가정한 UTC가 아니라 사용자 로컬 존(`Asia/Seoul`)이 IANA 이름으로
  명시된다.** 파싱이 훨씬 쉬워졌다. TZ 문자열을 그대로 IANA로 해석하면 됨.
- 리셋 시각이 현재보다 과거로 나오면(예: 새벽 시각) 다음 발생분으로 +1일 롤오버.
- 파싱 실패 시 `fallbackWaitHours`(기본 5h) 뒤 재시도.

## 훅 이벤트

- 에러 항목은 assistant 턴의 마지막 산출물로 기록되고, 그 뒤 CLI가 프롬프트로 돌아온다.
  따라서 **`Stop` 훅**이 트리거로 적합하다. 훅 stdin JSON에 `transcript_path`(절대경로),
  `session_id`, `hook_event_name`이 들어오므로, `Stop`에서 transcript 꼬리를 읽어
  마지막(또는 꼬리 N줄 중) `isApiErrorMessage:true` 항목을 찾으면 된다.
- `Stop`이 API 에러 시 실제로 발화하는지는 **T7 라이브 검증 항목**으로 남긴다(합성
  항목이 turn 종료로 기록되므로 발화한다고 보고 설계하되, 안 되면 pane 스크레이프 폴백).
- 서브에이전트 에러(`isSidechain:true` / `agentId` 존재)는 `SubagentStop`으로 오는데,
  herdr 관리 훅도 이를 무시한다. MVP는 **메인 세션(`isSidechain:false`)만** 재시도한다.

## CLAUDE_CONFIG_DIR / 인스턴스 분리

- 인스턴스별 config dir이 갈린다: `~/.ccs/instances/{enterprise,team}`.
- 각 인스턴스의 `settings.json`은 `~/.ccs/shared/settings.json` **심볼릭 링크**라 훅
  등록은 사실상 공유된다(한 번 등록하면 인스턴스 공통 적용). 단 이는 이 머신의 ccs 셋업
  특성이므로, install은 "현재 `CLAUDE_CONFIG_DIR`의 settings.json"을 대상으로 하고
  심볼릭이면 실경로 기준으로 중복 등록을 피한다.
- transcript는 `transcript_path`로 절대경로가 주어지므로, config dir이 어디로 갈리든
  **감지 단계에서 config dir을 되짚을 필요가 없다.** (열린 질문 해소)

## herdr 상태 경로가 막힌 근거

`src/detect/manifests/claude.toml`의 `state="blocked"` 규칙 전부:
`live_blocked_form`(enter to select/esc to cancel 폼), `dynamic_workflow_prompt`,
`bash_permission_prompt`, `generic_permission_prompt`, `legacy_no_prompt_blocker`.
전부 TUI 프롬프트 텍스트 매칭이며 레이트리밋 문구는 없다. 따라서 `herdr agent wait
--status blocked`로는 레이트리밋을 못 잡는다. (PLAN T1 연구 질문 및 2차 트리거 재평가 완료)

## 설계 반영 사항

1. 감지 코어는 tmux 캡처가 아니라 **transcript JSONL 파서**로 간다(원조 auto-retry와 갈림).
2. `isApiErrorMessage` + `error` 필드로 유형 분기: `rate_limit`→대기, `server_error`/
   `unknown`(overloaded)→백오프, `authentication_failed`/`model_not_found`→재시도 안 함.
3. 리셋 파서는 로컬 IANA TZ를 그대로 신뢰(UTC 가정 폐기).
4. 2차 트리거(herdr blocked)는 범위에서 제외, 폴백은 pane 스크레이프만.
