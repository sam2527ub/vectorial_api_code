"""
Contextual stimulus + category (tier-1) and category-only for replies (tier-2) — production (S3 + API).

* **S3 I/O** — :func:`stimulus_extraction_room_runner.run_stimulus_categorization_for_room`
* **LLM** — ``stimulus_extraction_llm`` via :mod:`app.services.ai_gateway_service`
* **Prompts** — ``prompts/*.txt`` under this package
* **Batched** (size :data:`stimulus_constants.BATCH_SIZE`); no mid-run S3 checkpoint in v1 (re-run to recover)
"""

from .errors import StimulusExtractionError
from .stimulus_extraction_request_handler import StimulusExtractionRequestHandler, request_handler
from .stimulus_extraction_room_runner import run_stimulus_categorization_for_room

__all__ = (
    "StimulusExtractionError",
    "StimulusExtractionRequestHandler",
    "request_handler",
    "run_stimulus_categorization_for_room",
)
