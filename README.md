# claude-auto-retry-herdr

[herdr](https://github.com/) 안에서 도는 Claude Code 세션이 레이트리밋(5시간 한도,
`API Error: 529` 등)에 걸리면, 리셋 시각까지 기다렸다가 자동으로 이어서 재시도하게
해주는 herdr 네이티브 도구.

기존 [`claude-auto-retry`](https://github.com/cheapestinference/claude-auto-retry)는
감지와 재시도 주입이 tmux(`capture-pane` / `send-keys`)에 묶여 있다. herdr는 tmux가
아니라 자체 소켓 기반 멀티플렉서라, 그 도구를 쓰려면 tmux 세션을 한 겹 더 끼워야 한다.
이 프로젝트는 그 중간 tmux 껍데기 없이 herdr 소켓 API(`herdr pane read` /
`herdr pane send-text` / `herdr agent` 등)만으로 같은 동작을 재현한다.

> 상태: 설계 단계. 구현 계획은 [`PLAN_claude-auto-retry-herdr.md`](./PLAN_claude-auto-retry-herdr.md) 참고.

## 왜 필요한가

- Claude Code를 herdr pane 안에서 실행한다.
- 레이트리밋에 걸려도 사람이 붙어서 재시도를 눌러줄 필요 없이, 리셋 시각에 자동으로
  이어가고 싶다.
- 상용 auto-retry의 tmux 중첩(herdr pane → tmux → claude)이 주는 키바인딩 충돌과
  pane 추적 혼선을 피하고 싶다.

## 동작 개요 (계획)

1. Claude Code 훅이 턴 종료 시 transcript를 검사해 레이트리밋 여부와 리셋 시각을 판별.
2. 감지되면 분리형 대기 프로세스를 띄워 리셋 시각 + 여유시간까지 대기.
3. 깨어나면 `herdr pane send-text` + Enter로 재시도 메시지를 주입해 세션을 이어감.
4. 주입 전 대상 pane의 foreground가 여전히 claude인지 확인하는 안전장치 포함.

자세한 구성 요소, 설정, 태스크는 PLAN 문서에 있다.
