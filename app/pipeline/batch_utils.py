"""Batch chunking and scheduling utilities."""

from typing import TypeVar

T = TypeVar("T")


def chunk_list(items: list[T], chunk_size: int) -> list[list[T]]:
    """Split a list into chunks of at most chunk_size."""
    return [
        items[i : i + chunk_size]
        for i in range(0, len(items), chunk_size)
    ]
