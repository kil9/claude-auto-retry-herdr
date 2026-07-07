# claude-auto-retry-herdr

[herdr](https://github.com/) 안에서 도는 Claude Code 세션이 레이트리밋(5시간/세션
한도, `API Error: 529` 등)에 걸리면, 리셋 시각까지 기다렸다가 자동으로 이어서
재시도하게 해주는 herdr 네이티브 도구.

기존 [`claude-auto-retry`](https://github.com/cheapestinference/claude-auto-retry)는
감지와 재시도 주입이 tmux(`capture-pane` / `send-keys`)에 묶여 있다. herdr는 tmux가
아니라 자체 소켓 기반 멀티플렉서라, 그 도구를 쓰려면 tmux 세션을 한 겹 더 끼워야 한다.
이 프로젝트는 그 중간 tmux 껍데기 없이, Claude Code 훅 + herdr 소켓 API
(`herdr pane send-text` / `send-keys` / `process-info`)만으로 같은 동작을 재현한다.

## 동작 방식

1. Claude Code `Stop` 훅이 턴 종료 시 transcript(JSONL) 꼬리를 검사한다. 마지막 턴이
   API 에러로 끝났으면(`isApiErrorMessage: true`) 에러 유형을 분기한다.
   - `rate_limit`(429, 세션/5시간 한도): 메시지의 `resets <시각> (<TZ>)`를 파싱해
     리셋 시각까지 대기.
   - `server_error`(529) / overloaded: 지수 백오프로 재시도.
   - `authentication_failed`(401) / `model_not_found`(404): 사용자 조치가 필요하므로
     **자동 재시도하지 않는다.**
2. 감지되면 마커 파일을 쓰고 `setsid`로 분리된 대기 프로세스를 띄운다. 관리할 데몬이 없다.
3. 대기 프로세스는 예약 시각까지 자고, 깨어나면 안전장치를 확인한 뒤
   `herdr pane send-text` + `send-keys Enter`로 재시도 메시지를 주입한다.
4. 안전장치: 주입 전 (a) 대상 pane이 아직 존재하는지, (b) transcript가 여전히 에러
   상태인지(사용자가 이미 이어갔으면 포기), (c) foreground 프로세스가 여전히 claude인지
   확인한다. 재시도 횟수 상한(`maxRetries`)과 최소 간격(`minRetryIntervalSeconds`)으로
   무한 재주입을 막는다.

감지 근거(실측)와 설계 결정은 [`docs/detection-notes.md`](./docs/detection-notes.md),
[`PLAN_claude-auto-retry-herdr.md`](./PLAN_claude-auto-retry-herdr.md) 참고.

## 요구 사항

- herdr 안에서 실행 중일 것 (`HERDR_ENV=1`, `HERDR_PANE_ID`, `HERDR_SOCKET_PATH`).
- `herdr` CLI가 PATH에 있을 것 (`HERDR_BIN`으로 재정의 가능).
- python3 (표준 라이브러리만 사용, 추가 런타임 없음).

## 설치

훅을 현재 Claude Code 설정(`$CLAUDE_CONFIG_DIR/settings.json`, 없으면
`~/.claude/settings.json`)의 `Stop` 훅 목록에 나란히 등록한다. herdr 관리 훅을 건드리지
않으며 멱등적이다.

```sh
bin/car-herdr install                       # 현재 CLAUDE_CONFIG_DIR
bin/car-herdr install --config-dir DIR ...  # 특정 config dir (반복 가능)
bin/car-herdr install --all-instances       # ~/.ccs/instances/* 전체
```

등록 후 새로 시작하는 Claude Code 세션부터 적용된다(진행 중 세션은 재시작 필요).

제거:

```sh
bin/car-herdr uninstall [--config-dir DIR | --all-instances]
```

## 사용

설치 후에는 자동으로 동작한다. 상태/로그 확인과 수동 재시도:

```sh
bin/car-herdr status        # 설정, 경로, 예약된 재시도 마커
bin/car-herdr logs          # 오늘 로그 (--date YYYY-MM-DD)
bin/car-herdr retry-now [--pane ID]   # 예약 대기를 무시하고 즉시 재시도
```

## 설정

`~/.config/car-herdr/config.json` (전 필드 optional, 누락 시 기본값). 경로는
`XDG_CONFIG_HOME` / `CAR_HERDR_CONFIG_DIR`로 재정의 가능.

```jsonc
{
  "maxRetries": 10,              // streak당 재시도 상한
  "marginSeconds": 120,          // 리셋 시각 이후 여유
  "fallbackWaitHours": 5,        // 리셋 시각 파싱 실패 시 대기
  "pollIntervalSeconds": 5,      // 대기 프로세스 폴링 간격
  "minRetryIntervalSeconds": 60, // 이 간격 안 재발은 streak로 묶어 cap 적용
  "retryMessage": "Continue where you left off. The previous attempt was rate limited.",
  "overloadedBackoffSeconds": [30, 60, 120, 300], // 529 지수 백오프
  "verifyForegroundProcess": true  // 주입 전 foreground=claude 확인
}
```

상태 파일(마커, 로그)은 `~/.local/state/car-herdr/`(`XDG_STATE_HOME` /
`CAR_HERDR_STATE_DIR`).

## 한계 / 비목표

- **메인 세션만** 재시도한다. 서브에이전트(`isSidechain`) 레이트리밋은 대상 밖.
- `Stop` 훅이 레이트리밋 시 실제 발화하는지는 라이브 세션에서 확인이 필요하다
  (본 저장소는 replay로 E2E 검증). 발화하지 않는 경로는 pane 스크레이프 폴백(Phase 2,
  T8)으로 보강 예정.
- tmux 지원 안 함(상용 도구가 담당). Claude Code 외 에이전트 미지원.
- herdr 자체 기능(pane 관리 등)은 재구현하지 않는다.

## 개발

```sh
python3 -m unittest discover -s tests   # 단위 테스트
tests/e2e_smoke.sh                      # herdr 안에서 전체 체인 E2E (수동)
```
