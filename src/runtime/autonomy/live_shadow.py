"""Optional, passive observation of *actual* Subject initiative projections.

An initiative revision is NOT necessarily an Identity revision. Preserve it as
source_revision and never present these observations as adopted commitments.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Mapping, Any


def observe_subject_initiative(initiative: Mapping[str, Any]) -> bool:
    """Record bounded candidate metadata (not freeform semantic goal text).

    Called only behind ERIS_AUTONOMY_SHADOW_GOALS=1. Any instrumentation
    failure is non-fatal, and never promotes a candidate to a goal.
    """
    if str(os.getenv('ERIS_AUTONOMY_SHADOW_GOALS', '0')).lower() not in ('1','yes','true'):
        return False
    try:
        db = Path(os.getenv('ERIS_AUTONOMY_GOAL_SHADOW_DB') or 'logs/autonomy_pending_shadow.sqlite')
        db.parent.mkdir(parents=True, exist_ok=True)
        revision = int(initiative.get('revision', 0))
        if revision < 0:
            return False
        rows = []
        for item in tuple(initiative.get('pending_intentions') or ())[:12]:
            if not isinstance(item, Mapping):
                continue
            if str(item.get('status') or 'pending') != 'pending':
                continue
            ident = str(item.get('intention_id') or '').strip()[:160]
            motive = str(item.get('motive_kind') or '')[:120]
            if not ident:
                continue
            # Avoid raw thoughts, goals and sensitive evidence in telemetry.
            rows.append((revision, ident, motive, 'pending', time.time()))
        con = sqlite3.connect(str(db), timeout=0.25, isolation_level=None)
        try:
            con.execute('PRAGMA busy_timeout=250')
            con.execute('''CREATE TABLE IF NOT EXISTS subject_candidate_observation (
                source_revision INTEGER NOT NULL, intention_id TEXT NOT NULL,
                motive_kind TEXT NOT NULL, state TEXT NOT NULL, observed_at REAL NOT NULL,
                PRIMARY KEY(source_revision,intention_id))''')
            con.execute('BEGIN IMMEDIATE')
            con.executemany('''INSERT OR IGNORE INTO subject_candidate_observation
                (source_revision,intention_id,motive_kind,state,observed_at) VALUES(?,?,?,?,?)''', rows)
            con.execute('COMMIT')
        finally:
            con.close()
        return True
    except Exception:
        return False
