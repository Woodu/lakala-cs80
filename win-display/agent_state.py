#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置 CS80 状态屏的 agent 状态（屏上那个「运行中/已完成…」+ 任务名 + 计时）。

    python agent_state.py running "重构 auth 中间件"
    python agent_state.py waiting
    python agent_state.py done
    python agent_state.py error "测试挂了"
    python agent_state.py idle

原理：写 %USERPROFILE%\\cs80_display\\state.json，QDisplay 服务每轮循环都会重读它。
所以任何程序（agent 收尾钩子、CI、shell 脚本）只要改这个文件，屏上立刻反映。
"""
import json
import os
import sys
import time

STATES = ("idle", "running", "waiting", "done", "error")
PATH = os.path.join(os.path.expanduser("~"), "cs80_display", "state.json")


def set_state(state, task=""):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    cur = {}
    try:
        with open(PATH, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        pass
    # 同一个任务且状态没变时保留 since，计时不重置
    same = cur.get("state") == state and (cur.get("task") or "") == (task or cur.get("task") or "")
    st = {
        "state": state,
        "task": task if task else (cur.get("task", "") if same else task),
        "since": cur.get("since", time.time()) if same else time.time(),
    }
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, PATH)
    return st


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STATES:
        print(__doc__)
        raise SystemExit(1)
    st = set_state(sys.argv[1], " ".join(sys.argv[2:]))
    print("state ->", st["state"], "|", st["task"])
