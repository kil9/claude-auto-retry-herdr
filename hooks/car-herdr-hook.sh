#!/bin/sh
# car-herdr custom hook (herdr 관리 훅 옆에 나란히 얹는 커스텀 훅).
# Claude Code Stop 훅에서 호출된다. stdin으로 훅 입력 JSON을 받아
# car-herdr hook 서브커맨드에 그대로 넘긴다. 실패해도 세션을 막지 않도록 항상 0 종료.
#
# 배선/파싱 로직은 T6에서 완성. 지금은 얇은 전달 래퍼.

set -u

# herdr pane 안에서만 의미 있음. 밖이면 조용히 통과.
[ "${HERDR_ENV:-}" = "1" ] || exit 0

HERE="$(cd "$(dirname "$0")" && pwd)"
CAR_HERDR="$HERE/../bin/car-herdr"

if [ -x "$CAR_HERDR" ]; then
  "$CAR_HERDR" hook 2>/dev/null || true
fi

exit 0
