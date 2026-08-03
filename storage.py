from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class TraceStore:
    """Persist trace runs through a non-blocking bounded queue."""

    def __init__(
        self,
        database_path: Path,
        *,
        max_runs: int = 200,
        retention_hours: int = 24,
        queue_size: int = 1000,
    ) -> None:
        self.database_path = database_path
        self.max_runs = max(10, min(int(max_runs), 100_000))
        self.retention_hours = max(0, min(int(retention_hours), 24 * 365))
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=max(10, min(int(queue_size), 100_000))
        )
        self.dropped_records = 0
        self.write_errors = 0
        self._started = False
        self._writer_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize the database and writer task."""
        async with self._start_lock:
            if self._started:
                return
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._initialize_database)
            self._writer_task = asyncio.create_task(
                self._writer(), name="llm_trace_sqlite_writer"
            )
            self._started = True

    def submit(self, operation: dict[str, Any]) -> bool:
        """Queue a write operation without blocking the LLM path.

        Args:
            operation: Storage operation payload.

        Returns:
            Whether the operation was accepted.
        """
        if not self._started:
            self.dropped_records += 1
            return False
        try:
            self.queue.put_nowait(operation)
            return True
        except asyncio.QueueFull:
            self.dropped_records += 1
            return False

    async def stop(self) -> None:
        """Drain queued writes and stop the writer task."""
        if not self._started:
            return
        await self.queue.put(None)
        if self._writer_task:
            await self._writer_task
        self._writer_task = None
        self._started = False

    async def list_runs(
        self, *, limit: int = 50, offset: int = 0, query: str = ""
    ) -> list[dict[str, Any]]:
        """List trace summaries newest first."""
        await self.start()
        return await asyncio.to_thread(
            self._list_runs_sync,
            max(1, min(int(limit), 200)),
            max(0, int(offset)),
            query.strip(),
        )

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Load one run and all ordered events."""
        await self.start()
        return await asyncio.to_thread(self._get_run_sync, run_id)

    async def clear(self) -> None:
        """Delete every stored run and event."""
        await self.start()
        await self.queue.join()
        await asyncio.to_thread(self._clear_sync)

    async def delete_run(self, run_id: str) -> bool:
        """Delete one trace run."""
        await self.start()
        await self.queue.join()
        return await asyncio.to_thread(self._delete_run_sync, run_id)

    async def _writer(self) -> None:
        while True:
            first = await self.queue.get()
            if first is None:
                self.queue.task_done()
                break
            batch = [first]
            while len(batch) < 100:
                try:
                    item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    self.queue.task_done()
                    try:
                        await asyncio.to_thread(self._write_batch, batch)
                    except Exception:
                        self.write_errors += len(batch)
                    finally:
                        for _ in batch:
                            self.queue.task_done()
                    return
                batch.append(item)
            try:
                await asyncio.to_thread(self._write_batch, batch)
            except Exception:
                self.write_errors += len(batch)
            finally:
                for _ in batch:
                    self.queue.task_done()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    span_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    provider_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL DEFAULT 'running',
                    event_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_started_at
                    ON runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_run_sequence
                    ON events(run_id, sequence);
                """
            )

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            for operation in batch:
                kind = operation.get("kind")
                if kind == "start":
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO runs (
                            run_id, span_id, session_id, sender_id, sender_name,
                            platform, provider_id, model, started_at, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
                        """,
                        (
                            operation["run_id"],
                            operation["span_id"],
                            operation["session_id"],
                            operation["sender_id"],
                            operation["sender_name"],
                            operation["platform"],
                            operation.get("provider_id", ""),
                            operation.get("model", ""),
                            operation["timestamp"],
                        ),
                    )
                elif kind == "event":
                    connection.execute(
                        """
                        INSERT INTO events (
                            run_id, sequence, timestamp, event_type, payload_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            operation["run_id"],
                            operation["sequence"],
                            operation["timestamp"],
                            operation["event_type"],
                            json.dumps(
                                operation.get("payload", {}),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE runs SET event_count = event_count + 1
                        WHERE run_id = ?
                        """,
                        (operation["run_id"],),
                    )
                elif kind == "finish":
                    connection.execute(
                        """
                        UPDATE runs
                        SET finished_at = ?, status = ?
                        WHERE run_id = ?
                        """,
                        (
                            operation["timestamp"],
                            operation.get("status", "completed"),
                            operation["run_id"],
                        ),
                    )
            self._prune(connection)

    def _prune(self, connection: sqlite3.Connection) -> None:
        if self.retention_hours:
            cutoff = time.time() - self.retention_hours * 3600
            old_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT run_id FROM runs WHERE started_at < ?", (cutoff,)
                )
            ]
            self._delete_ids(connection, old_ids)
        excess_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT run_id FROM runs
                ORDER BY started_at DESC
                LIMIT -1 OFFSET ?
                """,
                (self.max_runs,),
            )
        ]
        self._delete_ids(connection, excess_ids)

    @staticmethod
    def _delete_ids(connection: sqlite3.Connection, run_ids: list[str]) -> None:
        if not run_ids:
            return
        connection.executemany(
            "DELETE FROM events WHERE run_id = ?", [(x,) for x in run_ids]
        )
        connection.executemany(
            "DELETE FROM runs WHERE run_id = ?", [(x,) for x in run_ids]
        )

    def _list_runs_sync(
        self, limit: int, offset: int, query: str
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        params: list[Any] = []
        if query:
            sql += " WHERE session_id LIKE ? OR model LIKE ? OR provider_id LIKE ?"
            needle = f"%{query}%"
            params.extend((needle, needle, needle))
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(sql, params)]

    def _get_run_sync(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["events"] = []
            for event in connection.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY sequence, id",
                (run_id,),
            ):
                item = dict(event)
                try:
                    item["payload"] = json.loads(item.pop("payload_json"))
                except json.JSONDecodeError:
                    item["payload"] = {"error": "invalid stored JSON"}
                result["events"].append(item)
            return result

    def _clear_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM runs")

    def _delete_run_sync(self, run_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM runs WHERE run_id = ?", (run_id,)
            )
            connection.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
            return cursor.rowcount > 0
