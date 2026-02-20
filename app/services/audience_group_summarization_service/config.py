"""Configuration for Audience Group Summarization service."""

GROUP_MODEL = "anthropic/claude-sonnet-4.5"
GROUP_MODEL_SNAPSHOT = "claude-sonnet-4-5-20250929"  # Production stability
MAX_COMPLETION_TOKENS = 1200
TRAITS_MAX_COMPLETION_TOKENS = 2000

REQUIRED_TRAIT_TITLES = [
    "Skills & Expertise",
    "Working Style",
    "Motivations & Values",
    "Pain Points & Needs",
    "Organizational Leadership & Psychographic Profile",
]

DEFAULT_GROUP_SYSTEM = (
    "You are an expert at analyzing groups of LinkedIn profiles and generating "
    "comprehensive, insightful high-level summaries. Write detailed, informative "
    "summaries that capture collective patterns and insights."
)
DEFAULT_TRAITS_SYSTEM = (
    "You are an expert at analyzing professional profiles and generating structured "
    "trait data. Always return valid JSON only, no additional text."
)


class AudienceGroupSummarizationConfig:
    """Configuration for group summary and traits generation."""

    def __init__(
        self,
        group_model: str = GROUP_MODEL,
        group_model_snapshot: str = GROUP_MODEL_SNAPSHOT,
        max_completion_tokens: int = MAX_COMPLETION_TOKENS,
        traits_max_completion_tokens: int = TRAITS_MAX_COMPLETION_TOKENS,
        required_trait_titles: list = None,
    ):
        self.group_model = group_model
        self.group_model_snapshot = group_model_snapshot
        self.max_completion_tokens = max_completion_tokens
        self.traits_max_completion_tokens = traits_max_completion_tokens
        self.required_trait_titles = required_trait_titles or list(REQUIRED_TRAIT_TITLES)
