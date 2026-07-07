# PLAN: claude-auto-retry-herdr

herdr 안에서 도는 Claude Code 세션이 레이트리밋(5시간 한도, `API Error: 529` 등)에
걸렸을 때, 리셋 시각까지 기다렸다가 자동으로 재시도 메시지를 넣어 이어가게 하는
herdr 네이티브 도구.

기존 [`claude-auto-retry`](https://github.com/cheapestinference/claude-auto-retry)는
감지(`tmux capture-pane`)와 주입(`tmux send-keys`)이 전부 tmux에 묶여 있어, herdr
pane 안에서 쓰려면 tmux 세션을 한 겹 더 끼워야 한다. 이 프로젝트는 그 중간 tmux
껍데기를 없애고 herdr 소켓 API만으로 같은 동작을 재현한다.

## 배경과 제약

- herdr는 tmux가 아니라 자체 소켓 기반 멀티플렉서다 (`HERDR_SOCKET_PATH`, pane id
  `w8:p7` 형식). 상위 프로세스 체인에 tmux가 없다.
- herdr는 각 pane에서 도는 에이전트의 상태를 이미 추적한다. Claude Code용
  integration 훅(`herdr integration install claude` 이 설치하는 `herdr-agent-state.sh`)이
  Claude Code 훅 이벤트를 받아 `herdr pane report-agent ... --state working|idle|blocked`로
  전달하고, `herdr agent list` / `herdr api snapshot` 이 `agent_status` 로 노출한다.
- 그 integration 훅은 "이 파일을 수정하지 말고 옆에 커스텀 훅을 두라"고 명시한다.
  따라서 우리 훅은 herdr 관리 훅과 나란히 얹으면 되고, herdr 재설치 시 덮어써지지 않는다.
- Claude Code 훅 입력(JSON, stdin)에는 `hook_event_name`, `session_id`,
  `transcript_path`, (서브에이전트면) `agent_id` 가 들어온다. 레이트리밋의 근거는
  `transcript_path` JSONL 꼬리에서 찾는다.

## herdr CLI 원시 기능 (이 도구가 쓰는 것)

| 용도 | 명령 |
| --- | --- |
| pane 출력 스크레이핑 | `herdr pane read <pane_id> --source recent --lines N` |
| 텍스트 주입 | `herdr pane send-text <pane_id> <text>` |
| 키 주입(Enter 등) | `herdr pane send-keys <pane_id> Enter` |
| 명령+Enter 한 번에 | `herdr pane run <pane_id> <command>` |
| 패턴 등장까지 대기 | `herdr wait output <pane_id> --match <re> --regex --timeout MS` |
| 에이전트 상태 대기 | `herdr agent wait <target> --status blocked --timeout MS` |
| 전체 상태 스냅샷 | `herdr api snapshot`, `herdr agent list` |
| pane 메타(제목 등) | `herdr pane get <pane_id>`, `herdr pane report-metadata ...` |

`agent send` 은 리터럴 텍스트만 쓰고 Enter를 안 넣으므로, 제출까지 하려면
`pane send-text` + `send-keys Enter` 또는 `pane run` 을 쓴다.

## 설계 결정

### 트리거: 훅 감지 우선, 상태/스크레이프 폴백

세 경로가 있고 신뢰도 순으로 조합한다.

1. 훅 이벤트 (1차): Claude Code `Stop`(및 실패 계열 이벤트)에서 `transcript_path`
   꼬리를 읽어 레이트리밋/`API Error 429/529`/`5-hour limit reached ... resets`
   패턴을 찾는다. 있으면 리셋 시각을 파싱해 마커를 남긴다. 오탐이 적고 이벤트
   기반이라 폴링 낭비가 없다.
2. herdr 상태 (2차): claude integration 이 레이트리밋을 `blocked` 로 보고하는지
   확인(연구 과제 T1). 보고한다면 `herdr agent wait --status blocked` 로 훅 없이도
   트리거 가능.
3. pane 스크레이프 (폴백): 훅이 못 잡는 경로(예: 훅 이벤트가 안 뜨는 실패)를 위해
   `herdr pane read` 로 주기 폴링하며 동일 패턴을 매칭. 상용 도구가 쓰는 방식과 동일.

MVP는 1번(훅)만으로 충분히 동작하게 만들고, 2/3은 이후 단계에서 보강한다.

### 실행 형태: 훅 + 분리형 대기 프로세스 (MVP) → 데몬 (Phase 2)

- MVP: 훅이 레이트리밋을 감지하면 `setsid` 로 분리된 대기 프로세스를 띄운다.
  이 프로세스는 리셋 시각 + margin 까지 자고, 깨어나면 `herdr pane send-text`
  + `send-keys Enter` 로 재시도 메시지를 주입한 뒤 종료한다. 관리할 데몬이 없어
  가장 단순하다.
- Phase 2(선택): herdr 서버당 1개 데몬이 `agent list` 를 폴링/구독해 모든 pane의
  레이트리밋을 중앙에서 처리. 서버/머신 재시작에도 예약된 재시도가 살아남고
  (마커 파일 기반 복구), 상태바 표시를 붙이기 쉽다.

### 주입 안전장치

- 주입 직전 대상 pane의 foreground 프로세스가 여전히 claude 인지 `herdr pane
  process-info` 로 확인한다. 사용자가 이미 다른 걸 하고 있으면 주입하지 않는다.
- 대상 pane이 사라졌으면(닫힘) 조용히 포기하고 마커를 정리한다.
- 재시도 횟수 상한(`maxRetries`)과 최소 간격을 둬 무한 재주입을 막는다.

### 리셋 시각 파싱

- `5-hour limit reached - resets 3pm (UTC)` 같은 메시지에서 시각/타임존을 뽑아
  대기 시간을 계산. IANA 타임존과 서머타임을 고려. 파싱 실패 시 `fallbackWaitHours`
  (기본 5시간) 뒤 재시도.
- `API Error: 529`(overloaded)류는 리셋 시각이 없으므로 짧은 백오프(지수, 상한)로 재시도.

## 구성 요소

```
claude-auto-retry-herdr/
  bin/car-herdr              # CLI 진입점 (install / status / logs / retry-now / uninstall)
  hooks/car-herdr-hook.sh    # Claude Code 훅에서 호출, transcript 검사 → 마커 + 대기 프로세스 spawn
  lib/detect.*               # 레이트리밋/에러 패턴 매칭, 리셋 시각 파싱
  lib/inject.*               # herdr pane send-text/send-keys 래퍼 + 안전장치
  lib/waiter.*               # 분리형 대기 프로세스 본체
  lib/config.*               # ~/.config/car-herdr/config.json 로드(전 필드 optional, 기본값 폴백)
  logs/                      # YYYY-MM-DD.log
  PLAN_claude-auto-retry-herdr.md
  README.md
```

구현 언어는 herdr CLI를 셸로 호출하는 특성상 Node.js(기존 auto-retry 자산 참고 용이)
또는 순수 셸+python 중 택1. T2에서 확정.

## 설정 (`~/.config/car-herdr/config.json`, 전 필드 optional)

```jsonc
{
  "maxRetries": 10,
  "marginSeconds": 120,          // 리셋 시각 이후 여유
  "fallbackWaitHours": 5,        // 리셋 시각 파싱 실패 시
  "pollIntervalSeconds": 5,      // 폴백 스크레이프/데몬용
  "retryMessage": "Continue where you left off. The previous attempt was rate limited.",
  "overloadedBackoffSeconds": [30, 60, 120, 300]  // 529류 백오프
}
```

## 태스크 (순차 실행, 태스크별 커밋)

- T1 연구: Claude Code가 레이트리밋 시 어떤 훅 이벤트를 언제 발생시키는지, transcript
  JSONL에 한도 메시지가 어떤 형태로 기록되는지, herdr integration 이 그걸 `blocked`
  로 보고하는지 실측 정리. `CLAUDE_CONFIG_DIR`가 인스턴스별로 갈리는 점(.ccs/instances/*)
  도 함께 확인. 결과를 `docs/detection-notes.md` 로.
- T2 스캐폴딩: 저장소 구조/언어 확정, `bin/car-herdr` 골격, config 로더, 로깅.
- T3 감지: transcript 꼬리 파서 + 패턴 매칭 + 리셋 시각/타임존 파싱, 단위 테스트.
- T4 주입: `herdr pane` 래퍼(send-text/send-keys/process-info 안전장치) 구현, 실제
  pane 대상 스모크 테스트.
- T5 대기 프로세스: `setsid` 분리형 waiter, 마커 파일 read/clear, 재시도 상한.
- T6 훅 배선: `hooks/car-herdr-hook.sh` + `car-herdr install`(herdr 관리 훅 옆에
  커스텀 훅을 얹고, 여러 `CLAUDE_CONFIG_DIR` 처리), `uninstall`.
- T7 E2E 검증: 레이트리밋을 인위적으로 재현(또는 로그 리플레이)해 감지→대기→주입
  전체 흐름을 herdr pane에서 확인.
- T8 폴백/상태 (선택, Phase 2): 스크레이프 폴백 + herdr 상태 트리거, 데몬화,
  `pane report-metadata` 로 상태바 표시.
- T9 문서: README 사용법, 설치/제거, 설정, 한계.

## 열린 질문 / 연구 필요

- Claude Code에 실패 전용 훅(StopFailure 류)이 실제로 있는지, 아니면 `Stop`에서
  transcript로 판별해야 하는지 (T1).
- 최신 Claude Code가 5시간 한도에 대해 자체 auto-wait/재개를 이미 하는 범위. 그
  경우 이 도구는 자체 재개가 없는 케이스(하드 한도, 529, 세션 종료형)에 집중.
- herdr에 이벤트 스트림 구독이 있는지, 없으면 데몬은 `agent list` 폴링으로 간다.
- `pane send-text` 후 제출 키가 Enter 하나로 충분한지(붙여넣기 모드/줄바꿈 처리).

## 비목표

- tmux 지원(상용 도구가 이미 담당).
- Claude Code 외 에이전트(codex 등) 지원 (구조는 확장 가능하게 두되 범위 밖).
- herdr 자체 기능(pane 관리 등) 재구현.
