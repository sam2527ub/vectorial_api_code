"""Split lists into fixed-size batches (e.g. for Apify batch runs)."""
from typing import List, TypeVar

T = TypeVar("T")


def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    """Split a list into chunks of at most chunk_size."""
    if chunk_size <= 0:
        return [lst] if lst else []
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]
