"""Claude Code 훅 등록/제거.

herdr 관리 훅(settings.json의 herdr 항목)을 건드리지 않고, 같은 settings.json의
`Stop` 훅 리스트에 우리 커스텀 항목을 나란히 추가한다. 커맨드 문자열로 멱등 판정.

settings.json이 심볼릭 링크(ccs instance → shared)면 realpath로 실경로를 편집해
링크를 깨지 않고, 여러 인스턴스가 같은 shared를 가리켜도 중복 등록하지 않는다.
"""

import datetime
import json
import os

# 등록되는 훅 스크립트(저장소 위치 절대경로). Claude가 Stop 시 stdin으로 훅 JSON을 준다.
HOOK_SCRIPT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "car-herdr-hook.sh")
)
HOOK_EVENT = "Stop"


def resolve_settings_path(config_dir=None):
    """대상 settings.json 실경로. config_dir 미지정 시 $CLAUDE_CONFIG_DIR → ~/.claude."""
    cd = config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
    if not cd:
        cd = os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.realpath(os.path.join(cd, "settings.json"))


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return None  # 깨진 파일: 호출부가 판단


def _backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.car-herdr.bak-{stamp}"
    try:
        with open(path, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
        return bak
    except OSError:
        return None


def _write(path, data):
    tmp = path + ".car-herdr.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _entries_have_our_hook(entries):
    for entry in entries:
        for hook in entry.get("hooks", []) or []:
            if hook.get("command") == HOOK_SCRIPT:
                return True
    return False


def install(settings_path):
    """반환: (status, message). status ∈ installed|already|error."""
    data = _load(settings_path)
    if data is None:
        return "error", f"settings.json 파싱 실패: {settings_path}"
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return "error", "settings.json의 hooks 형식이 예상과 다릅니다"
    entries = hooks.setdefault(HOOK_EVENT, [])
    if not isinstance(entries, list):
        return "error", f"settings.json의 hooks.{HOOK_EVENT} 형식이 예상과 다릅니다"
    if _entries_have_our_hook(entries):
        return "already", f"이미 등록됨: {settings_path}"
    _backup(settings_path)
    entries.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": HOOK_SCRIPT}],
    })
    _write(settings_path, data)
    return "installed", f"등록 완료: {settings_path}"


def uninstall(settings_path):
    """반환: (status, message). status ∈ removed|absent|error."""
    data = _load(settings_path)
    if data is None:
        return "error", f"settings.json 파싱 실패: {settings_path}"
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or HOOK_EVENT not in hooks:
        return "absent", f"등록된 훅 없음: {settings_path}"
    entries = hooks.get(HOOK_EVENT) or []
    kept = []
    removed = False
    for entry in entries:
        orig = entry.get("hooks") or []
        sub = [h for h in orig if h.get("command") != HOOK_SCRIPT]
        if len(sub) != len(orig):
            removed = True
            if sub:  # 다른 훅이 남으면 유지, 우리 것만 있던 항목은 통째로 제거
                entry = dict(entry)
                entry["hooks"] = sub
                kept.append(entry)
        else:
            kept.append(entry)
    if not removed:
        return "absent", f"등록된 훅 없음: {settings_path}"
    if kept:
        hooks[HOOK_EVENT] = kept
    else:
        del hooks[HOOK_EVENT]
    _backup(settings_path)
    _write(settings_path, data)
    return "removed", f"제거 완료: {settings_path}"
