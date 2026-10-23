from contextlib import contextmanager
from pathlib import Path
from typing import Any

import asyncio
import json
import os
import sqlite3
import time
import uuid

from .security import redact


def uid() -> str:
    return uuid.uuid4().hex


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class BudgetExceeded(RuntimeError):
    pass


class Storage:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / "bililife.sqlite3"
        with self.connect() as db:
            db.executescript((Path(__file__).parent / "migrations/001_initial.sql").read_text(encoding="utf-8"))
            db.execute("INSERT OR IGNORE INTO schema_version VALUES(1,?)", (time.time(),))
            db.executescript((Path(__file__).parent / "migrations/002_module_controls.sql").read_text(encoding="utf-8"))
            db.execute("INSERT OR IGNORE INTO schema_version VALUES(2,?)", (time.time(),))
            db.executescript((Path(__file__).parent / "migrations/003_autonomous_life.sql").read_text(encoding="utf-8"))
            db.execute("INSERT OR IGNORE INTO schema_version VALUES(3,?)", (time.time(),))
            db.executescript((Path(__file__).parent / "migrations/004_comment_reading.sql").read_text(encoding="utf-8"))
            db.execute("INSERT OR IGNORE INTO schema_version VALUES(4,?)", (time.time(),))
        self.path.chmod(0o600)
        self.lock_file = None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    async def call(self, method, *args, **kwargs):
        return await asyncio.to_thread(getattr(self, method), *args, **kwargs)

    def lock(self):
        self.lock_file = (self.directory / "executor.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock_file.seek(0)
                self.lock_file.write(b"0")
                self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("同一数据目录已有 BiliLife 执行器") from None

    def unlock(self):
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def get(self, key, default=None):
        rows = self.rows("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key, value):
        self.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dump(value)))

    def update_model_test(self,role,entry):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM kv WHERE key='model_provider_tests'").fetchone()
            state=json.loads(row[0]) if row else {}
            old=state.get(role,{})
            if entry['state']=='queued' and old.get('task_id')==entry.get('task_id') and old.get('state')!='queued':return
            state[role]=entry
            db.execute("INSERT INTO kv VALUES('model_provider_tests',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(state),))

    def audit(self, kind, data):
        self.execute("INSERT INTO audit(kind,data,created) VALUES(?,?,?)", (kind, dump(redact(data)), time.time()))

    def reserve(self, day, resources):
        """在一次事务内检查并预扣全部资源，避免并发超额。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for name, (amount, limit) in resources.items():
                if amount < 0:
                    raise ValueError("资源预扣不能为负")
                row = db.execute("SELECT used FROM budget WHERE day=? AND resource=?", (day, name)).fetchone()
                if (row[0] if row else 0) + amount > limit:
                    raise BudgetExceeded(f"当日 {name} 预算耗尽")
            for name, (amount, _) in resources.items():
                db.execute("INSERT INTO budget VALUES(?,?,?) ON CONFLICT(day,resource) DO UPDATE SET used=used+excluded.used", (day, name, amount))

    def refund(self, day, resource, amount):
        self.execute("UPDATE budget SET used=MAX(0,used-?) WHERE day=? AND resource=?", (max(0, amount), day, resource))

    def add_task(self, kind, payload, day, earliest, deadline, duration=300, plan_id=None, dependencies=None, task_id=None):
        task_id, now = task_id or uid(), time.time()
        self.execute("INSERT INTO tasks(id,plan_id,day,type,state,payload,earliest,deadline,next_run,duration,dependencies,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (task_id, plan_id, day, kind, "queued", dump(payload), earliest, deadline, earliest, duration, dump(dependencies or []), now, now))
        return task_id

    def save_plan(self, day, proposal, tasks, reason, revise=False):
        now, plan_id = time.time(), uid()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT id,version FROM plans WHERE day=? AND active=1", (day,)).fetchone()
            if old and not revise:
                return old["id"]
            version = 1
            if old:
                version = old["version"] + 1
                db.execute("UPDATE plans SET active=0 WHERE id=?", (old["id"],))
                db.execute("UPDATE tasks SET cancel_requested=1,state=CASE WHEN state='running' THEN state ELSE 'cancelled' END,updated=? WHERE plan_id=? AND state NOT IN ('succeeded','failed','cancelled','skipped','expired')", (now, old["id"]))
            proposal.update(plan_id=plan_id, date=day, version=version)
            db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,?)", (plan_id, day, version, 1, reason, dump(proposal), now))
            for task in tasks:
                db.execute("INSERT INTO tasks(id,plan_id,day,type,state,payload,earliest,deadline,next_run,duration,dependencies,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (task["id"], plan_id, day, task["type"], "queued", dump(task["payload"]), task["earliest"], task["deadline"], task["earliest"], task["duration"], dump(task["dependencies"]), now, now))
        return plan_id

    def recover(self):
        now = time.time()
        with self.connect() as db:
            # 只有持有本机排他文件锁的实例可调用；旧实例已经退出，未知发送绝不重试。
            db.execute("UPDATE operations SET state='unknown',error='进程中断，等待核对',updated=? WHERE state IN ('pending','sending')", (now,))
            db.execute("UPDATE tasks SET state=CASE WHEN cancel_requested=1 THEN 'cancelled' WHEN type='publish' THEN 'unknown' WHEN deadline<=? THEN 'expired' ELSE 'queued' END,owner=NULL,lease_until=NULL,updated=? WHERE state='running'", (now, now))
            db.execute("UPDATE attempts SET finished=?,outcome='interrupted' WHERE finished IS NULL", (now,))
            db.execute("UPDATE adaptive_decisions SET state='interrupted' WHERE state='requesting'")
            db.execute("UPDATE comment_reads SET state='failed',reason='上次进程中断，等待任务恢复' WHERE state='reading'")

    def claim(self, owner, paused=False, active=True):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE tasks SET state='expired',updated=? WHERE state IN ('queued','retry') AND deadline<=?", (now, now))
            candidates = db.execute("SELECT * FROM tasks WHERE state IN ('queued','retry') AND cancel_requested=0 AND earliest<=? AND next_run<=? AND deadline>? AND (?=0 OR type IN ('login','account','reconcile','model_test')) AND (?=1 OR type NOT IN ('discover','watch','explore','read_comments','publish','replies')) ORDER BY CASE type WHEN 'reconcile' THEN 0 WHEN 'account' THEN 1 WHEN 'model_test' THEN 1 ELSE 2 END,earliest,created LIMIT 100", (now, now, now, int(paused), int(active))).fetchall()
            for row in candidates:
                dependencies = json.loads(row["dependencies"])
                if dependencies:
                    states = [db.execute("SELECT state FROM tasks WHERE id=?", (x,)).fetchone() for x in dependencies]
                    if any(x is None or x[0] in ("cancelled", "skipped", "expired", "failed") for x in states):
                        db.execute("UPDATE tasks SET state='skipped',error='依赖任务未完成',updated=? WHERE id=?", (now, row["id"]))
                        continue
                    if not all(x[0] == "succeeded" for x in states):
                        continue
                db.execute("UPDATE tasks SET state='running',owner=?,lease_until=?,attempts=attempts+1,updated=? WHERE id=?", (owner, now + row["duration"] + 30, now, row["id"]))
                db.execute("INSERT INTO attempts(task_id,number,started) VALUES(?,?,?)", (row["id"], row["attempts"] + 1, now))
                result = dict(row)
                result.update(owner=owner, attempts=row["attempts"] + 1)
                return result
        return None

    def finish(self, task, state, result=None, error=None, next_run=None):
        now = time.time()
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?,result=?,error=?,next_run=COALESCE(?,next_run),owner=NULL,lease_until=NULL,updated=? WHERE id=? AND owner=? AND state='running'", (state, dump(redact(result)), error, next_run, now, task["id"], task["owner"]))
            db.execute("UPDATE attempts SET finished=?,outcome=?,error=? WHERE task_id=? AND number=?", (now, state, error, task["id"], task["attempts"]))

    def cancel(self, task_id, skipped=False):
        return self.execute("UPDATE tasks SET cancel_requested=1,state=CASE WHEN state='running' THEN state ELSE ? END,updated=? WHERE id=? AND state NOT IN ('succeeded','failed','cancelled','skipped','expired','unknown')", ("skipped" if skipped else "cancelled", time.time(), task_id))

    def delete_task(self, task_id, reason):
        now=time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
            if row is None:
                raise ValueError("日程不存在")
            if row["type"]=="publish" and row["state"] in ("running","unknown"):
                raise ValueError("已发送或结果未知的操作必须继续核对，不能删除")
            db.execute("UPDATE tasks SET cancel_requested=1,state=CASE WHEN state='running' THEN state WHEN state IN ('queued','retry','paused','blocked_model','blocked_policy','blocked_login','budget_exhausted','timeout') THEN 'cancelled' ELSE state END,updated=? WHERE id=?",(now,task_id))
            db.execute("INSERT OR REPLACE INTO task_tombstones VALUES(?,?,?)",(task_id,reason,now))
            db.execute("INSERT INTO schedule_edits VALUES(?,?,?,?,?,?,?)",(uid(),row["day"],"delete",task_id,dump(dict(row)),reason,now))
        return dict(row)

    def backup(self):
        directory = self.directory / "backups"
        directory.mkdir(exist_ok=True, mode=0o700)
        target = directory / f"bililife-{time.time_ns()}.sqlite3"
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o600)
        return str(target)

    def prune(self, days, cache, cache_hours):
        cutoff = time.time() - days * 86400
        with self.connect() as db:
            for table in ("audit", "model_calls", "reply_events"):
                db.execute(f"DELETE FROM {table} WHERE created<?", (cutoff,))
        for path in cache.glob("*"):
            if path.is_file() and path.stat().st_mtime < time.time() - cache_hours * 3600:
                path.unlink()
