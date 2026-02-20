"""Split URL lists into fixed-size batches."""
from typing import List


def split_urls_into_batches(urls: List[str], batch_size: int) -> List[List[str]]:
    """Split URLs into batches of at most batch_size."""
    if batch_size <= 0:
        return [urls] if urls else []
    return [urls[i : i + batch_size] for i in range(0, len(urls), batch_size)]
