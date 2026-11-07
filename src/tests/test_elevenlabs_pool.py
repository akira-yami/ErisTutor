# -*- coding: utf-8 -*-
"""test_elevenlabs_pool.py — Unit test kiểm tra ElevenLabs SQLite Pool & Rotation."""

import os
import tempfile
import time
from pathlib import Path
import pytest

from runtime.elevenlabs_pool_db import ElevenPoolDB


@pytest.fixture
def temp_pool_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_eleven_pool.db"
        pool = ElevenPoolDB(db_path=db_path)
        yield pool


def test_add_and_list_account(temp_pool_db):
    pool = temp_pool_db
    ok, msg, acc = pool.add_account(
        refresh_token="test_token_1234567890abcdef",
        label="Test Account 1",
        notes="Ghi chú thử nghiệm",
        auto_sync=False,
    )
    assert ok is True
    assert acc is not None
    assert acc.label == "Test Account 1"
    assert acc.character_limit == 10000
    assert acc.character_count == 0

    accounts = pool.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].refresh_token == "test_token_1234567890abcdef"


def test_active_account_rotation_and_usage(temp_pool_db):
    pool = temp_pool_db
    pool.add_account("token_1", label="Acc 1", auto_sync=False)
    pool.add_account("token_2", label="Acc 2", auto_sync=False)

    active = pool.get_active_account()
    assert active is not None
    assert active.label in {"Acc 1", "Acc 2"}

    # Record usage that exhausts Acc 1
    pool.record_usage(active.id, 10000)
    updated_acc1 = pool.get_account_by_id(active.id)
    assert updated_acc1.status == "exhausted"
    assert updated_acc1.remaining_characters == 0

    # Next active account should be Acc 2
    next_active = pool.get_active_account()
    assert next_active is not None
    assert next_active.id != active.id


def test_auto_reset_passed_timestamp(temp_pool_db):
    pool = temp_pool_db
    ok, msg, acc = pool.add_account("token_reset_test", label="Reset Acc", auto_sync=False)
    assert acc is not None

    past_reset = time.time() - 100.0
    pool.mark_exhausted(acc.id, cooldown_seconds=3600, reset_timestamp=past_reset)

    exhausted_acc = pool.get_account_by_id(acc.id)
    assert exhausted_acc.status == "exhausted"

    # List accounts triggers auto-heal for passed reset_at
    accounts = pool.list_accounts()
    recovered_acc = pool.get_account_by_id(acc.id)
    assert recovered_acc.status == "active"
    assert recovered_acc.character_count == 0


def test_summary_stats(temp_pool_db):
    pool = temp_pool_db
    pool.add_account("tok_1", label="Acc 1", auto_sync=False)
    pool.add_account("tok_2", label="Acc 2", auto_sync=False)

    stats = pool.get_summary_stats()
    assert stats["total_accounts"] == 2
    assert stats["active_accounts"] == 2
    assert stats["total_characters_limit"] == 20000
    assert stats["total_characters_remaining"] == 20000
