"""拆号另立（partition split-off）。

全局口径（唯一架构）：**新分区继承原分区现行温层与所属片库**。

- 新分区起始温层 = 原分区当前温层；新分区归属片库 = 原分区所属片库（不改库、不改温层）。
- 被拆素材段由原分区改挂新分区；湖对象键归属同步由原分区改为新分区。
- 原分区若仍有残留段则保持「在册」；若一段不剩则关「已拆」，并在回执中记录关册结果。
- 改挂、在册变迁、回执、对象键归属全部在同一写事务内完成，同成败；失败整体回滚。
- 禁止只开新号：至少改挂一段，否则拒绝。
- 对象键沿用，绝不新建目录行，杜绝键双挂；并发以 BEGIN IMMEDIATE 串行化，杜绝负段数。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import write_db
from app.services.core import now, partition_row, require_tier, vault_partition_count


def list_splits(conn=None):
    def _q(c):
        rows = c.execute(
            '''SELECT s.*, sp.title AS source_title, np.title AS new_title
               FROM partition_splits s
               JOIN partitions sp ON sp.id=s.source_partition_id
               JOIN partitions np ON np.id=s.new_partition_id
               ORDER BY s.id DESC'''
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["改挂段数"] = c.execute(
                "SELECT COUNT(*) AS n FROM split_moves WHERE split_id=?", (r["id"],)
            ).fetchone()["n"]
            out.append(d)
        return out

    if conn is not None:
        return _q(conn)
    from app.db import db

    with db() as c:
        return _q(c)


def list_moves(split_id: int):
    from app.db import db

    with db() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM split_moves WHERE split_id=? ORDER BY id", (split_id,)
            ).fetchall()
        ]


def split_partition(
    source_partition_id: int,
    new_part_key: str,
    clip_ids: list[int],
    title: str | None = None,
):
    new_part_key = (new_part_key or "").strip()
    if not new_part_key:
        raise HTTPException(400, detail="新分区键不能为空")
    ids = sorted({int(x) for x in (clip_ids or [])})
    if not ids:
        raise HTTPException(400, detail="禁止只开新号：至少选择一段素材改挂新分区")

    # 单写事务 + 先取写锁：并发拆号在此串行，后到者读到已变更的原册状态。
    with write_db() as conn:
        src = partition_row(conn, source_partition_id)
        if src["status"] != "在册":
            raise HTTPException(400, detail="仅「在册」分区可拆号另立")
        source_tier = require_tier(src["tier"])  # 新分区继承，非请求参数，杜绝口径分叉

        if conn.execute("SELECT 1 FROM partitions WHERE part_key=?", (new_part_key,)).fetchone():
            raise HTTPException(400, detail="新分区键已存在")

        # 迁移执行中时温层归属未定，拆号继承的温层会漂移，直接拒绝。
        open_job = conn.execute(
            "SELECT 1 FROM migrate_jobs WHERE partition_id=? AND status='执行中'",
            (source_partition_id,),
        ).fetchone()
        if open_job:
            raise HTTPException(400, detail="原分区有执行中迁移，拒绝拆号")

        src_clips = conn.execute(
            "SELECT * FROM clips WHERE partition_id=? ORDER BY id",
            (source_partition_id,),
        ).fetchall()
        owned = {c["id"]: c for c in src_clips}
        stray = [i for i in ids if i not in owned]
        if stray:
            raise HTTPException(400, detail=f"素材段不属于原分区：{stray}")
        if len(src_clips) == 0:
            raise HTTPException(400, detail="原分区无素材段，不可拆号")

        moved = [owned[i] for i in ids]
        remaining = [c for c in src_clips if c["id"] not in set(ids)]
        source_closes = len(remaining) == 0

        # 归属片库容量：原册若关「已拆」则空出一席，新册补位、净增 0。
        vault = conn.execute("SELECT * FROM vaults WHERE id=?", (src["vault_id"],)).fetchone()
        if not vault or not vault["active"]:
            raise HTTPException(400, detail="原分区所属片库间不可用")
        used = vault_partition_count(conn, src["vault_id"])
        projected = used + 1 - (1 if source_closes else 0)
        if projected > vault["capacity"]:
            raise HTTPException(400, detail="片库间分区已满，拒绝拆号另立")

        # 键唯一性兜底：任一段键若已被别处引用即拒绝，确保不双挂。
        for c in moved:
            clash = conn.execute(
                "SELECT 1 FROM clips WHERE object_key=? AND partition_id<>?",
                (c["object_key"], source_partition_id),
            ).fetchone()
            if clash:
                raise HTTPException(400, detail=f"对象键已被其它分区占用：{c['object_key']}")

        created = now()
        new_title = (title or "").strip() or f"{src['title']}·拆号"
        # 先立新册（继承原温层与原所属库），再落回执与改挂，全部同事务。
        ncur = conn.execute(
            '''INSERT INTO partitions(part_key, title, release_day, vault_id, tier,
                                     object_prefix, status, created_at)
               VALUES (?,?,?,?,?,?, '在册', ?)''',
            (
                new_part_key, new_title, src["release_day"], src["vault_id"],
                source_tier, src["object_prefix"], created,
            ),
        )
        new_pid = ncur.lastrowid

        cur = conn.execute(
            '''INSERT INTO partition_splits(
                   source_partition_id, new_partition_id, source_part_key, new_part_key,
                   source_vault_id, new_vault_id, source_tier, new_tier,
                   moved_clip_count, remaining_clip_count, source_status_after,
                   status, created_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                source_partition_id, new_pid, src["part_key"], new_part_key,
                src["vault_id"], src["vault_id"], source_tier, source_tier,
                len(moved), len(remaining), "已拆" if source_closes else "在册",
                "执行中", created, "拆号另立：继承原温层与片库",
            ),
        )
        split_id = cur.lastrowid

        for c in moved:
            # 改挂：段归属 → 新分区；对象键不变，仅迁所有权，绝不新增目录行。
            conn.execute(
                "UPDATE clips SET partition_id=? WHERE id=? AND partition_id=?",
                (new_pid, c["id"], source_partition_id),
            )
            moved_now = conn.execute(
                "SELECT changes() AS n"
            ).fetchone()["n"]
            if moved_now != 1:
                raise HTTPException(409, detail=f"素材段 {c['id']} 改挂冲突，已回滚")
            conn.execute(
                "UPDATE inventory_objects SET owner_partition_id=? WHERE object_key=?",
                (new_pid, c["object_key"]),
            )
            conn.execute(
                '''INSERT INTO split_moves(
                       split_id, clip_id, object_key, from_partition_id, to_partition_id, created_at)
                   VALUES (?,?,?,?,?,?)''',
                (split_id, c["id"], c["object_key"], source_partition_id, new_pid, created),
            )

        # 在册变迁：仅当原册一段不剩才关「已拆」；否则原册留在册，新册并入。
        if source_closes:
            conn.execute(
                "UPDATE partitions SET status='已拆' WHERE id=? AND status='在册'",
                (source_partition_id,),
            )
            if conn.execute("SELECT changes() AS n").fetchone()["n"] != 1:
                raise HTTPException(409, detail="原分区关册冲突，已回滚")

        conn.execute(
            "UPDATE partition_splits SET status='已完成' WHERE id=?",
            (split_id,),
        )

        return {
            "split_id": split_id,
            "status": "已完成",
            "source_partition_id": source_partition_id,
            "new_partition_id": new_pid,
            "new_part_key": new_part_key,
            "source_tier": source_tier,
            "new_tier": source_tier,
            "vault_id": src["vault_id"],
            "moved_clip_count": len(moved),
            "remaining_clip_count": len(remaining),
            "source_status_after": "已拆" if source_closes else "在册",
            "moved_object_keys": [c["object_key"] for c in moved],
        }
