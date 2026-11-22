from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS vaults (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  capacity INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS partitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  release_day TEXT NOT NULL,
  vault_id INTEGER NOT NULL,
  tier TEXT NOT NULL,
  object_prefix TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '在册',
  created_at TEXT NOT NULL,
  FOREIGN KEY(vault_id) REFERENCES vaults(id)
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  bytes INTEGER NOT NULL,
  entered_day TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id)
);
CREATE TABLE IF NOT EXISTS migrate_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  from_tier TEXT NOT NULL,
  to_tier TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id)
);
CREATE TABLE IF NOT EXISTS retention_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  max_age_days INTEGER NOT NULL,
  action TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS destroy_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  policy_id INTEGER NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id),
  FOREIGN KEY(policy_id) REFERENCES retention_policies(id)
);
CREATE TABLE IF NOT EXISTS inventory_objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_key TEXT NOT NULL UNIQUE,
  bytes INTEGER NOT NULL,
  vault_name TEXT NOT NULL,
  scanned_at TEXT NOT NULL,
  owner_partition_id INTEGER
);
CREATE TABLE IF NOT EXISTS verify_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  drift_count INTEGER NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS partition_splits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_partition_id INTEGER NOT NULL,
  new_partition_id INTEGER NOT NULL,
  source_part_key TEXT NOT NULL,
  new_part_key TEXT NOT NULL,
  source_vault_id INTEGER NOT NULL,
  new_vault_id INTEGER NOT NULL,
  source_tier TEXT NOT NULL,
  new_tier TEXT NOT NULL,
  moved_clip_count INTEGER NOT NULL,
  remaining_clip_count INTEGER NOT NULL,
  source_status_after TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(source_partition_id) REFERENCES partitions(id),
  FOREIGN KEY(new_partition_id) REFERENCES partitions(id)
);
CREATE TABLE IF NOT EXISTS split_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  split_id INTEGER NOT NULL,
  clip_id INTEGER NOT NULL,
  object_key TEXT NOT NULL,
  from_partition_id INTEGER NOT NULL,
  to_partition_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(split_id, clip_id),
  FOREIGN KEY(split_id) REFERENCES partition_splits(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
'''


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_db():
    """串行化写事务：BEGIN IMMEDIATE 先取写锁，并发拆号/迁移按序执行。

    拆号需要在同一事务内先读后写（读原分区状态、算残留段、写新分区与改挂），
    默认的 DEFERRED 事务会让两个并发拆号都读到「在册」再各自写，造成负段数或双加。
    """
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _upgrade(conn) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(inventory_objects)").fetchall()}
    if "owner_partition_id" not in cols:
        conn.execute("ALTER TABLE inventory_objects ADD COLUMN owner_partition_id INTEGER")
    # 旧库回填对象键归属：以素材段当前所在分区为准，避免拆号前遗留空归属。
    conn.execute(
        '''UPDATE inventory_objects
           SET owner_partition_id = (
               SELECT c.partition_id FROM clips c
               WHERE c.object_key = inventory_objects.object_key
           )
           WHERE owner_partition_id IS NULL'''
    )


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _upgrade(conn)
