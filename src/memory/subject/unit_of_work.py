# -*- coding: utf-8 -*-
"""SQLite unit of work with explicit transaction ownership."""
from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Type


class UnitOfWork:
    def __init__(self, connection: sqlite3.Connection, lock=None) -> None:
        self.connection = connection
        self._lock = lock
        self._entered = False

    def __enter__(self) -> "UnitOfWork":
        if self._lock is not None:
            self._lock.acquire()
        self.connection.execute("BEGIN IMMEDIATE")
        self._entered = True
        return self

    def commit(self) -> None:
        if self._entered:
            self.connection.commit()
            self._entered = False

    def rollback(self) -> None:
        if self._entered:
            self.connection.rollback()
            self._entered = False

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            if self._lock is not None:
                self._lock.release()
        return False
