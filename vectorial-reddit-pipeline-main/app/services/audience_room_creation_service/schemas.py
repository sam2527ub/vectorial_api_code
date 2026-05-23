"""Request/Response schemas for audience room creation."""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


class AudienceRoomCreationRequest(BaseModel):
    """Request schema for creating an audience room."""
    audience_room_name: str
    enterpriseName: Optional[str] = None
    userId: str
    source: str
    query: str
    users: List[Dict[str, Any]]
    search_results: List[Dict[str, Any]]
    activities: Optional[List[Dict[str, Any]]] = None
    activities_s3_url: Optional[str] = None
    audience_room_description: Optional[str] = None
    category: Optional[Literal["b2b", "b2c"]] = None
    subreddit_similarity_data: Optional[Dict[str, Any]] = None
    # --- Preview-then-Real workflow additions ---
    audience_room_id: Optional[str] = None
    """Pre-allocated stable room id shared between the preview and real runs.
    When present, the handler switches to idempotent upsert + replace-profiles
    mode instead of creating a fresh UUID."""
    is_preview: Optional[bool] = False
    """True when called by the preview pipeline. Triggers the READY-guard that
    skips the destructive upsert if the real pipeline has already finished."""
    is_preview_then_real: Optional[bool] = False
    """True when the caller participates in the Preview-then-Real flow.
    On the real side, enables the preview-only profile preservation merge."""