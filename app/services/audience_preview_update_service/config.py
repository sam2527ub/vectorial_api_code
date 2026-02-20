"""Configuration for Audience Preview Update service."""

PREVIEW_PROFILE_LIMIT = 5  # Number of profiles to include in preview


class AudiencePreviewUpdateConfig:
    """Configuration for preview update (profile limit for preview payload)."""

    def __init__(self, preview_profile_limit: int = PREVIEW_PROFILE_LIMIT):
        self.preview_profile_limit = preview_profile_limit
