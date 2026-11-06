"""Durable transport uncertainty, keyed by the exact unified session and illust.

Use guarded_send for one atomic image message (including all pages or a forward).
pending/unknown never expire or get removed by random-history retention. Terminal
rows retain the last receipt for cursor recovery, but do not forbid deliberate
requeries; callers still own their normal duplicate policies. Never split a batch
and resend the unblocked subset after a blocked outcome.

Manual reconciliation needs adapter evidence and an interval without active sends.
The token-bound async API is intended for maintenance tools; database.py exposes
the simpler session-scoped admin API. Age alone never authorizes automatic resend.
"""

import asyncio
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .random_empty_retry import is_send_outcome_unknown, is_send_timeout_after_accept


UNRESOLVED = ("pending", "unknown")
STALE_PENDING_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class DeliveryOutcome:
    status: Literal["sent", "unknown", "blocked", "failed"]
    error: Exception | None = None
    receipt: Any = None
    blocked_ids: frozenset[int] = frozenset()


def _ids(values):
    ids = tuple(dict.fromkeys(values))
    if not ids or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise ValueError("delivery requires positive integer illust IDs")
    return ids


def _session(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("delivery requires the exact nonempty unified session")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sent_cutoff(retention_days):
    if retention_days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=max(int(retention_days), 1))


def _recent_sent(row, cutoff):
    if cutoff is None or row["state"] != "sent":
        return False
    timestamp = datetime.fromisoformat(row["updated_at"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp >= cutoff


class DeliveryLedger:
    """Short independent SQLite transactions; no connection spans a transport await."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS delivery_uncertainty (
                session_id TEXT NOT NULL,
                illust_id INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','unknown','sent','failed')),
                attempt_token TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                receipt_id TEXT,
                detail TEXT,
                PRIMARY KEY (session_id, illust_id)
            )""")
            connection.commit()
            yield connection
        finally:
            connection.close()

    def _rows(self, connection, session_id, ids):
        # Query one key at a time to avoid SQLite's variable limit on large forwards.
        rows = {}
        for illust_id in ids:
            row = connection.execute(
                "SELECT * FROM delivery_uncertainty WHERE session_id=? AND illust_id=?",
                (session_id, illust_id),
            ).fetchone()
            if row is not None:
                rows[illust_id] = dict(row)
        return rows

    def status(self, session_id, ids):
        session_id, ids = _session(session_id), _ids(ids)
        with self._connection() as connection:
            return self._rows(connection, session_id, ids)

    def begin(self, session_id, ids, sent_retention_days=None, reject_sent=False):
        session_id, ids = _session(session_id), _ids(ids)
        cutoff = _sent_cutoff(sent_retention_days)
        token = uuid4().hex
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = self._rows(connection, session_id, ids)
            blocked = frozenset(
                key for key, row in rows.items()
                if row["state"] in UNRESOLVED
                or (reject_sent and row["state"] == "sent")
                or _recent_sent(row, cutoff)
            )
            if blocked:
                connection.rollback()
                return None, blocked
            for illust_id in ids:
                connection.execute(
                    """INSERT INTO delivery_uncertainty
                       (session_id,illust_id,state,attempt_token,updated_at)
                       VALUES (?,?,'pending',?,?)
                       ON CONFLICT(session_id,illust_id) DO UPDATE SET
                       state='pending',attempt_token=excluded.attempt_token,
                       updated_at=excluded.updated_at,receipt_id=NULL,detail=NULL""",
                    (session_id, illust_id, token, _now()),
                )
            connection.commit()
        return token, frozenset()

    def finish(self, session_id, ids, token, state, receipt_id=None, detail=None):
        if state not in ("sent", "unknown", "failed"):
            raise ValueError("invalid delivery outcome")
        session_id, ids = _session(session_id), _ids(ids)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for illust_id in ids:
                changed = connection.execute(
                    """UPDATE delivery_uncertainty SET state=?,updated_at=?,receipt_id=?,detail=?
                       WHERE session_id=? AND illust_id=? AND attempt_token=? AND state='pending'""",
                    (state, _now(), receipt_id, detail, session_id, illust_id, token),
                ).rowcount
                if changed != 1:
                    raise ValueError("delivery attempt changed; refusing stale finalization")
            connection.commit()

    def unresolved(self, session_id):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT * FROM delivery_uncertainty WHERE session_id=?
                   AND state IN ('pending','unknown') ORDER BY updated_at,illust_id""",
                (_session(session_id),),
            )]

    def reconcile(self, session_id, ids, token, resolution, note):
        """CAS resolution of unknown or stale pending; call only with no active sends."""
        if resolution not in ("sent", "failed") or not isinstance(note, str) or not note.strip():
            raise ValueError("manual reconciliation requires a result and evidence note")
        session_id, ids = _session(session_id), _ids(ids)
        cutoff = datetime.now(timezone.utc) - STALE_PENDING_AFTER
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = self._rows(connection, session_id, ids)
            for illust_id in ids:
                row = rows.get(illust_id)
                if row is None or row["attempt_token"] != token or row["state"] not in UNRESOLVED:
                    raise ValueError("delivery is not the expected unresolved attempt")
                if row["state"] == "pending" and datetime.fromisoformat(row["updated_at"]) > cutoff:
                    raise ValueError("delivery attempt is still actively pending")
            for illust_id in ids:
                connection.execute(
                    """UPDATE delivery_uncertainty SET state=?,updated_at=?,detail=?
                       WHERE session_id=? AND illust_id=? AND attempt_token=?""",
                    (resolution, _now(), note[:1000], session_id, illust_id, token),
                )
            connection.commit()


def get_delivery_ledger():
    # Lazy lookup keeps imports compatible with database test doubles and reloads.
    from astrbot.api.star import StarTools

    return DeliveryLedger(StarTools.get_data_dir("pixiv_search") / "delivery_uncertainty.db")


async def delivery_status(session_id, illust_ids, *, ledger=None):
    """Raise on lookup failure: callers must not interpret unreadable state as unsent."""
    return await asyncio.to_thread((ledger or get_delivery_ledger()).status, session_id, illust_ids)


async def unresolved_deliveries(session_id, *, ledger=None):
    return await asyncio.to_thread((ledger or get_delivery_ledger()).unresolved, session_id)


async def recent_sent_illust_ids(session_id, illust_ids, retention_days, *, ledger=None):
    rows = await delivery_status(session_id, illust_ids, ledger=ledger)
    cutoff = _sent_cutoff(retention_days)
    return frozenset(key for key, row in rows.items() if _recent_sent(row, cutoff))


async def resolve_delivery(session_id, illust_ids, *, attempt_token, resolution, note, ledger=None):
    await asyncio.to_thread(
        (ledger or get_delivery_ledger()).reconcile, session_id, illust_ids,
        attempt_token, resolution, note,
    )


def _receipt_id(receipt):
    value = receipt.get("message_id") if isinstance(receipt, dict) else getattr(receipt, "message_id", None)
    return str(value) if isinstance(value, (str, int)) else None


async def guarded_send(
    session_id, illust_ids, send, *, ledger=None, sent_retention_days=None, reject_sent=False
):
    """Persist pending, call a zero-arg awaitable once, then finalize with its token.

    Only exact False means explicit transport rejection; None is event.send success.
    Cancellation propagates and leaves pending. Any ledger failure blocks sending
    or leaves the committed pending record intact. No fallback is safe on unknown
    or blocked. Accepted sendMsg timeout retains the existing confirmed semantics.
    Scheduled callers may set sent_retention_days for an atomic history check;
    reject_sent=True blocks confirmed sends without expiry. Leave both unset for
    deliberate user requeries.
    """
    try:
        ids = _ids(illust_ids)
        ledger = ledger or get_delivery_ledger()
        token, blocked = await asyncio.to_thread(
            ledger.begin, session_id, ids, sent_retention_days, reject_sent
        )
    except Exception as error:
        return DeliveryOutcome("blocked", error=error)
    if token is None:
        return DeliveryOutcome("blocked", blocked_ids=blocked)

    receipt, error, state = None, None, "sent"
    try:
        receipt = await send()
        if receipt is False:
            state, error = "failed", RuntimeError("transport returned False; message was not accepted")
    except Exception as caught:
        error = caught
        if is_send_timeout_after_accept(caught):
            state = "sent"
        elif is_send_outcome_unknown(caught):
            state = "unknown"
        else:
            state = "failed"

    try:
        await asyncio.to_thread(
            ledger.finish, session_id, ids, token, state, _receipt_id(receipt),
            type(error).__name__ if error is not None else None,
        )
    except Exception as persistence_error:
        return DeliveryOutcome("unknown", error=persistence_error, receipt=receipt)
    return DeliveryOutcome(state, error=error, receipt=receipt)
