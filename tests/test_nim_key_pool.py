"""Tests for NIM API key pool with rotation and cooldown."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.normalization.nim_key_pool import NIMKeyPool, _COOLDOWN_SECONDS


def test_init_requires_at_least_one_key():
    with pytest.raises(ValueError, match="At least one NIM API key"):
        NIMKeyPool([])


def test_key_count():
    pool = NIMKeyPool(["k1", "k2", "k3"])
    assert pool.key_count == 3


@pytest.mark.asyncio
async def test_round_robin_rotation():
    pool = NIMKeyPool(["k1", "k2", "k3"])

    indices = []
    for _ in range(6):
        idx, _ = await pool.get_client()
        indices.append(idx)

    # Should cycle through 0, 1, 2, 0, 1, 2
    assert indices == [0, 1, 2, 0, 1, 2]
    await pool.close_all()


@pytest.mark.asyncio
async def test_skips_cooled_down_key():
    pool = NIMKeyPool(["k1", "k2", "k3"])

    # Mark key 0 as rate limited
    pool.mark_rate_limited(0)

    idx, _ = await pool.get_client()
    assert idx == 1  # Skipped key 0

    idx, _ = await pool.get_client()
    assert idx == 2

    idx, _ = await pool.get_client()
    assert idx == 1  # Still skipping key 0

    await pool.close_all()


@pytest.mark.asyncio
async def test_cooldown_expiry():
    pool = NIMKeyPool(["k1", "k2"])

    # Mark key 0 as rate limited with a tiny cooldown
    with patch("app.normalization.nim_key_pool.time") as mock_time:
        now = time.monotonic()
        mock_time.monotonic.return_value = now
        pool.mark_rate_limited(0)

        # Advance past cooldown
        mock_time.monotonic.return_value = now + _COOLDOWN_SECONDS + 1

        idx, _ = await pool.get_client()
        assert idx == 0  # Key 0 should be available again

    await pool.close_all()


@pytest.mark.asyncio
async def test_all_keys_cooled_down_waits():
    pool = NIMKeyPool(["k1", "k2"])

    now = time.monotonic()
    with patch("app.normalization.nim_key_pool.time") as mock_time:
        mock_time.monotonic.return_value = now
        pool.mark_rate_limited(0)
        pool.mark_rate_limited(1)

        # First call to get_client will see all in cooldown
        # Second call (after sleep) should return key 0 (lower expiry)
        call_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            # After sleep, advance time past cooldown
            mock_time.monotonic.return_value = now + _COOLDOWN_SECONDS + 1

        with patch("app.normalization.nim_key_pool.asyncio.sleep", mock_sleep):
            idx, _ = await pool.get_client()
            assert call_count == 1  # Should have slept once
            assert idx == 0  # Soonest key to recover

    await pool.close_all()


@pytest.mark.asyncio
async def test_close_all():
    pool = NIMKeyPool(["k1", "k2"])

    # Create clients by getting them
    _, client1 = await pool.get_client()
    _, client2 = await pool.get_client()

    await pool.close_all()

    assert client1.is_closed
    assert client2.is_closed
    assert pool._clients == {}
    assert pool._cooldowns == {}


@pytest.mark.asyncio
async def test_client_reuse():
    pool = NIMKeyPool(["k1"])

    _, client1 = await pool.get_client()
    _, client2 = await pool.get_client()

    # Same key should reuse the same client instance
    assert client1 is client2
    await pool.close_all()
