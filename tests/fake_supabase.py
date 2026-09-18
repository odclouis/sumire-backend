"""An in-memory fake of the supabase-py table().select/insert/update/delete chain.

Faithful enough (full-row representation on write, filter-then-fetch on select, array
containment via .contains()) to exercise app/db.py's real logic in tests without a live
Postgres connection.
"""

import uuid
from copy import deepcopy
from typing import Any


class FakeResponse:
    """Mimics postgrest's APIResponse: `.data` (rows) and `.count` (when requested)."""

    def __init__(self, data: list[dict[str, Any]], count: int | None = None) -> None:
        self.data = data
        self.count = count


class FakeQuery:
    """A single chained query against one fake table."""

    def __init__(self, table: "FakeTable") -> None:
        self.table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._op: str | None = None
        self._payload: dict[str, Any] | None = None
        self._count: str | None = None

    def select(self, cols: str, count: str | None = None) -> "FakeQuery":
        self._count = count
        return self

    def insert(self, payload: dict[str, Any]) -> "FakeQuery":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> "FakeQuery":
        self._op = "update"
        self._payload = payload
        return self

    def delete(self) -> "FakeQuery":
        self._op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "FakeQuery":
        self._filters.append(("eq", col, val))
        return self

    def ilike(self, col: str, val: Any) -> "FakeQuery":
        self._filters.append(("ilike", col, val))
        return self

    def contains(self, col: str, values: list[Any]) -> "FakeQuery":
        self._filters.append(("contains", col, list(values)))
        return self

    def order(self, col: str, desc: bool = False) -> "FakeQuery":
        self._order = (col, desc)
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._limit = n
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, col, val in self._filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "ilike" and str(row.get(col, "")).lower() != str(val).lower():
                return False
            if kind == "contains":
                current = row.get(col) or []
                if not all(v in current for v in val):
                    return False
        return True

    def execute(self) -> FakeResponse:
        rows = self.table.rows
        if self._op == "insert":
            new_row = dict(self._payload or {})
            new_row.setdefault("id", str(uuid.uuid4()))
            rows.append(new_row)
            return FakeResponse([deepcopy(new_row)])
        if self._op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self._payload or {})
            return FakeResponse([deepcopy(r) for r in matched])
        if self._op == "delete":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                rows.remove(r)
            return FakeResponse([deepcopy(r) for r in matched])

        matched = [r for r in rows if self._matches(r)]
        count = len(matched) if self._count else None
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: r.get(col) or "", reverse=desc)
        if self._limit:
            matched = matched[: self._limit]
        return FakeResponse([deepcopy(r) for r in matched], count=count)


class FakeTable:
    """Backing store for one fake table."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []


class FakeClient:
    """Drop-in stand-in for a supabase-py Client, passed to app.db.get_client via monkeypatch."""

    def __init__(self) -> None:
        self._tables: dict[str, FakeTable] = {}

    def table(self, name: str) -> FakeQuery:
        if name not in self._tables:
            self._tables[name] = FakeTable()
        return FakeQuery(self._tables[name])
