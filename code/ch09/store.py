"""第 9 章：会话状态怎么落盘。

一个教学版存储层：文件系统 + JSON，不依赖 Redis 或数据库。
三个键定一条会话：user_id / agent_id / session_id。

目录结构：
    .sessions/<user_id>/<agent_id>/<session_id>.json     完整 AgentState
    .sessions/<user_id>/<agent_id>/<session_id>.meta.json 会话列表用的元信息
"""

import json
import os
import time
from typing import Any

from agentscope.state import AgentState


class JsonStateStore:
    """把 AgentState 存成 JSON 文件。"""

    def __init__(self, root: str = ".sessions") -> None:
        self.root = root

    # ---------- 路径 ----------

    def _dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self.root, user_id, agent_id)

    def _path(self, user_id: str, agent_id: str, session_id: str, meta: bool = False) -> str:
        suffix = ".meta.json" if meta else ".json"
        return os.path.join(self._dir(user_id, agent_id), f"{session_id}{suffix}")

    # ---------- 存 ----------

    def save(
        self,
        user_id: str,
        agent_id: str,
        state: AgentState,
        title: str = "",
    ) -> None:
        directory = self._dir(user_id, agent_id)
        os.makedirs(directory, exist_ok=True)

        # 完整状态：model_dump_json 会把 Msg、Task 这些嵌套对象一起展开
        with open(self._path(user_id, agent_id, state.session_id), "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))

        # 元信息：会话列表只需要这几个字段，不必读整份状态
        meta = {
            "session_id": state.session_id,
            "title": title or self._guess_title(state),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(state.context),
            "task_count": len(state.tasks_context.tasks),
            "summary_len": len(state.summary or ""),
        }
        with open(self._path(user_id, agent_id, state.session_id, meta=True), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---------- 取 ----------

    def load(self, user_id: str, agent_id: str, session_id: str) -> AgentState | None:
        path = self._path(user_id, agent_id, session_id)
        if not os.path.exists(path):
            return None

        with open(path, encoding="utf-8") as f:
            state = AgentState.model_validate_json(f.read())

        # 恢复时必须清掉的三样东西，理由见 9.3
        state.reply_context.structured_schema = None
        state.reply_context.structured_output = None
        state.reply_context.cur_iter = 0
        return state

    def list_sessions(self, user_id: str, agent_id: str) -> list[dict[str, Any]]:
        directory = self._dir(user_id, agent_id)
        if not os.path.isdir(directory):
            return []

        sessions = []
        for name in os.listdir(directory):
            if not name.endswith(".meta.json"):
                continue
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                sessions.append(json.load(f))
        sessions.sort(key=lambda m: m["updated_at"], reverse=True)
        return sessions

    def delete(self, user_id: str, agent_id: str, session_id: str) -> bool:
        removed = False
        for meta in (False, True):
            path = self._path(user_id, agent_id, session_id, meta=meta)
            if os.path.exists(path):
                os.remove(path)
                removed = True
        return removed

    # ---------- 辅助 ----------

    @staticmethod
    def _guess_title(state: AgentState, limit: int = 24) -> str:
        """拿第一条用户消息当标题。"""
        for msg in state.context:
            if getattr(msg, "role", "") == "user":
                text = (msg.get_text_content() or "").strip().replace("\n", " ")
                if text:
                    return text[:limit]
        return "新会话"
