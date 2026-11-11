"""Transactional local numbering and recoverable publication journal."""

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path


class NumberStore:
    def __init__(self, runtime_root, policy):
        self.root = Path(runtime_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "numbering.sqlite3"
        self.policy = policy
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS counters(day TEXT PRIMARY KEY, last INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations(
                    identity TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL,
                    day TEXT NOT NULL, company TEXT NOT NULL, batch TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publications(
                    identity TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publication_history(
                    id INTEGER PRIMARY KEY, identity TEXT NOT NULL, digest TEXT NOT NULL,
                    payload TEXT NOT NULL, committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS publication_history_by_identity ON publication_history(identity);
                INSERT INTO publication_history(identity,digest,payload)
                    SELECT p.identity,p.digest,p.payload FROM publications p
                    WHERE p.status='committed' AND NOT EXISTS
                      (SELECT 1 FROM publication_history h WHERE h.identity=p.identity AND h.digest=p.digest AND h.payload=p.payload);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.execute("PRAGMA busy_timeout=20000")
        return db

    def reserve(self, batch_id, company_id, shipping_date):
        if self.policy.get("confirmed") is not True:
            raise ValueError("编号起点和策略尚未确认")
        if not batch_id or not company_id:
            raise ValueError("批次及公司标识不能为空")
        day = date.fromisoformat(shipping_date).isoformat()
        prefix = self.policy.get("prefix", "OR_")
        start = self.policy.get("daily_starts", {}).get(day, self.policy.get("daily_start", 0))
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", prefix):
            raise ValueError("订单编号前缀无效")
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start < 999999:
            raise ValueError("编号起点须为0至999998的整数")
        identity = hashlib.sha256(json.dumps([batch_id, company_id, day], ensure_ascii=False).encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT number FROM reservations WHERE identity=?", (identity,)).fetchone()
            if previous:
                return previous[0]
            stored = db.execute("SELECT last FROM counters WHERE day=?", (day,)).fetchone()
            last = stored[0] if stored else start
            if last >= 999999:
                raise ValueError("当日六位流水已用尽")
            number = f"{prefix}{day.replace('-', '')}{last + 1:06d}"
            db.execute("INSERT INTO counters VALUES(?,?) ON CONFLICT(day) DO UPDATE SET last=excluded.last", (day, last + 1))
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?)", (identity, number, day, company_id, batch_id))
            return number

    def prepare(self, identity, digest, payload):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest,status,payload FROM publications WHERE identity=?", (identity,)).fetchone()
            if previous and previous[:2] == (digest, "committed") and json.loads(previous[2]) == payload:
                return
            db.execute("INSERT INTO publications VALUES(?,?,?,?) ON CONFLICT(identity) DO UPDATE SET digest=excluded.digest,status=excluded.status,payload=excluded.payload",
                       (identity, digest, "prepared", json.dumps(payload, ensure_ascii=False)))

    def commit(self, identity, digest, payload):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            text = json.dumps(payload, ensure_ascii=False)
            count = db.execute("UPDATE publications SET status='committed',payload=? WHERE identity=? AND digest=? AND status='prepared'",
                               (text, identity, digest)).rowcount
            if not count:
                raise ValueError("没有匹配的待提交导出事务")
            db.execute("INSERT INTO publication_history(identity,digest,payload) VALUES(?,?,?)", (identity, digest, text))

    def publication(self, identity):
        with self._connect() as db:
            result = db.execute("SELECT digest,status,payload FROM publications WHERE identity=?", (identity,)).fetchone()
        return None if result is None else {"digest": result[0], "status": result[1], "payload": json.loads(result[2])}

    def publication_history(self, identity):
        with self._connect() as db:
            rows = db.execute("SELECT digest,payload FROM publication_history WHERE identity=? ORDER BY id", (identity,))
            return [{"digest": digest, "payload": json.loads(payload)} for digest, payload in rows]
