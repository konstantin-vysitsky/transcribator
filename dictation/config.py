from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS: dict[str, Any] = {
    "hotkey": "f10",
    "hotkey_mode": "hold",
    "model": "v3_e2e_rnnt",
    "remove_fillers": True,
    "cpu_threads": 6,
    "paste_append_space": True,
    "paste_auto_enter": True,
    "sound_feedback": True,
    "min_utterance_s": 0.3,
    "autostart": False,
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
        return dict(DEFAULTS)
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    merged = dict(DEFAULTS)
    merged.update(data)
    merged["cpu_threads"] = max(1, min(10, int(merged.get("cpu_threads", 6))))
    return merged


def save_config(data: dict[str, Any]) -> None:
    merged = dict(DEFAULTS)
    merged.update(data)
    merged["cpu_threads"] = max(1, min(10, int(merged.get("cpu_threads", 6))))
    CONFIG_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
