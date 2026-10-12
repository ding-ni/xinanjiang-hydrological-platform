"""State names and favourites in SQLite; numerical checkpoints stay immutable."""
from __future__ import annotations

from common import now
from state_integrity import validated_parent_state


class StateRepository:
    def __init__(self, manager):
        self.manager = manager
        self.database = manager.database

    def checkpoint(self, state_id):
        # Validate the identifier before constructing any path.
        import re
        if not isinstance(state_id, str) or not re.fullmatch(r"STATE-JOB-[A-Za-z0-9_-]+", state_id):
            raise ValueError("状态编号无效")
        return validated_parent_state(self.manager.runtime_root / "states" / f"{state_id}.json",
                                      self.manager.root, state_id)

    def metadata(self):
        with self.database.connect() as db:
            return {r["state_id"]: {"name": r["name"], "favorite": bool(r["favorite"]),
                                   "revision": r["revision"], "updatedAt": r["updated_at"]}
                    for r in db.execute("SELECT * FROM state_metadata")}

    def update(self, state_id, body):
        self.checkpoint(state_id)
        if not isinstance(body, dict) or set(body) != {"name", "favorite", "revision"}:
            raise ValueError("请提供状态名称、收藏和修订号")
        name, favorite, revision = body["name"], body["favorite"], body["revision"]
        if not isinstance(name, str) or len(name.strip()) > 80:
            raise ValueError("状态名称最多 80 个字符")
        if not isinstance(favorite, bool) or type(revision) is not int or revision < 0:
            raise ValueError("收藏或修订号格式无效")
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT revision FROM state_metadata WHERE state_id=?", (state_id,)).fetchone()
            if revision != (old[0] if old else 0):
                raise RuntimeError("状态名称已更新，请刷新后保存")
            row = {"name": name.strip(), "favorite": favorite, "revision": revision + 1, "updatedAt": now()}
            db.execute("INSERT INTO state_metadata VALUES (?,?,?,?,?) ON CONFLICT(state_id) DO UPDATE SET name=excluded.name, favorite=excluded.favorite, revision=excluded.revision, updated_at=excluded.updated_at",
                       (state_id, row["name"], int(favorite), row["revision"], row["updatedAt"]))
        return row
