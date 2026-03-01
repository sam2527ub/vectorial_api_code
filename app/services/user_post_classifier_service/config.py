"""Configuration for User Post Classifier service (async classifier jobs)."""

DEFAULT_BATCH_SIZE = 10  # Number of profiles per batch
CLASSIFY_POSTS_BATCH_SIZE = 20  # Posts per batch when calling classifier


class UserPostClassifierConfig:
    """Configuration for async post classification."""

    def __init__(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        classify_batch_size: int = CLASSIFY_POSTS_BATCH_SIZE,
        classifier_model: str = "groq/llama-3.3-70b-versatile",
    ):
        self.batch_size = batch_size
        self.classify_batch_size = classify_batch_size
        # Model used via AI Gateway (default/fallback when gateway config is missing)
        self.classifier_model = classifier_model
