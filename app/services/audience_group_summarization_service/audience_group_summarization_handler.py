"""Handler for Audience Group Summarization: generate group summary and traits from profile summaries."""
import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import logger, s3_client, s3_bucket
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3, upload_json_to_s3
from app.utils.message_utils import split_prompt_into_messages
from app.services.ai_gateway_service import ai_gateway
from app.services.dynamic_context_window_management_service import context_manager
from app import database
from app.services.audience_group_summarization_service.config import (
    AudienceGroupSummarizationConfig,
    DEFAULT_GROUP_SYSTEM,
    DEFAULT_TRAITS_SYSTEM,
)


class AudienceGroupSummarizationHandler:
    """Generate group summary and traits for an audience room from profile summaries."""

    def __init__(self, config: Optional[AudienceGroupSummarizationConfig] = None):
        self.config = config or AudienceGroupSummarizationConfig()

    def _validate_dependencies(self) -> None:
        ensure_db_available("audience")
        from app.config import anthropic_client
        if not anthropic_client:
            raise HTTPException(
                status_code=503,
                detail="Anthropic client not initialized. Please set ANTHROPIC_API_KEY.",
            )
        if not s3_client or not s3_bucket:
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
            )

    def _collect_profile_summaries(self, audience_room_id: str, enterprise_name: Optional[str]) -> tuple:
        """Fetch room with profiles, then build profile_summaries list and companies set. Returns (room, description_key, profile_summaries, companies, profiles_processed, profiles_skipped)."""
        audience_room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=True, enterprise_name=enterprise_name
        )
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(
                status_code=404,
                detail=f"No profiles found in audience room {audience_room_id}",
            )
        if not audience_room.descriptionS3Url:
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        description_key = extract_s3_key_from_url(audience_room.descriptionS3Url)
        if not description_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format for audience room description")

        profile_summaries: List[Dict[str, Any]] = []
        companies = set()
        profiles_processed = 0
        profiles_skipped = 0

        for profile in profiles:
            try:
                if not profile.profileDescriptionS3Url:
                    logger.warning(f"Profile {profile.id} has no description URL, skipping")
                    profiles_skipped += 1
                    continue
                profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
                if not profile_key:
                    logger.warning(f"Profile {profile.id} has invalid description URL, skipping")
                    profiles_skipped += 1
                    continue
                profile_data = fetch_json_from_s3(profile_key)
                profile_summary = profile_data.get("summary")
                if not profile_summary:
                    logger.warning(f"Profile {profile.id} has no summary, skipping")
                    profiles_skipped += 1
                    continue
                profile_summaries.append({
                    "name": profile.profileName,
                    "summary": profile_summary,
                    "company": profile_data.get("current_company"),
                })
                if profile_data.get("current_company"):
                    companies.add(profile_data.get("current_company"))
                profiles_processed += 1
            except Exception as e:
                logger.error(f"Error fetching profile {profile.id} description: {e}")
                profiles_skipped += 1
                continue

        if not profile_summaries:
            raise HTTPException(
                status_code=400,
                detail=f"No profile summaries found. Please generate profile summaries first using /api/v1/audience-rooms/{audience_room_id}/generate-summaries/async",
            )
        return audience_room, description_key, profile_summaries, companies, profiles_processed, profiles_skipped

    def _validate_traits_response(self, traits_data: Dict[str, Any]) -> None:
        """Validate traits JSON structure. Raises ValueError on invalid structure."""
        if not isinstance(traits_data, dict) or "traits" not in traits_data:
            raise ValueError("Invalid traits JSON structure: missing 'traits' key")
        if not isinstance(traits_data["traits"], list) or len(traits_data["traits"]) != 5:
            raise ValueError(
                f"Invalid traits JSON structure: expected 5 traits, got {len(traits_data.get('traits', []))}"
            )
        required_titles = self.config.required_trait_titles
        received_titles = [trait.get("title") for trait in traits_data["traits"]]
        if set(received_titles) != set(required_titles):
            raise ValueError(f"Invalid trait titles. Expected: {required_titles}, Got: {received_titles}")
        for trait in traits_data["traits"]:
            if "keywordTags" not in trait or "descriptions" not in trait:
                raise ValueError(f"Trait '{trait.get('title')}' missing required fields")
            if not isinstance(trait["keywordTags"], list) or not isinstance(trait["descriptions"], list):
                raise ValueError(f"Trait '{trait.get('title')}' has invalid keywordTags or descriptions format")
            if len(trait["keywordTags"]) != len(trait["descriptions"]):
                raise ValueError(
                    f"Trait '{trait.get('title')}' has mismatched keywordTags and descriptions counts"
                )

    async def generate_group_summary(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate group summary and traits for an audience room from profile summaries.
        Fetches profiles and their S3 descriptions, builds combined summaries, calls Claude
        for group summary and traits, then updates room description in S3 and DB.
        Returns the same response shape as POST /api/v1/audience-rooms/{id}/generate-group-summary.
        """
        self._validate_dependencies()

        (
            audience_room,
            description_key,
            profile_summaries,
            companies,
            profiles_processed,
            profiles_skipped,
        ) = self._collect_profile_summaries(audience_room_id, enterprise_name)

        room_description_data = fetch_json_from_s3(description_key)
        profiles = audience_room.profiles or []
        logger.info(f"Generating group summary for {len(profiles)} profiles in audience room {audience_room_id}")

        combined_summaries = "\n\n".join([
            f"{idx + 1}. {p['name']} ({p.get('company', 'N/A')}):\n{p['summary']}"
            for idx, p in enumerate(profile_summaries)
        ])
        company_list = ", ".join(sorted(companies)) if companies else "various companies"
        company_type = company_list if len(companies) <= 3 else f"{len(companies)} companies"

        from prompts import group_summary_prompt
        full_group_prompt = group_summary_prompt.format(
            total_profiles=len(profile_summaries),
            company_type=company_type,
            company_list=company_list,
            combined_summaries=combined_summaries,
        )
        system_message, user_prompt = split_prompt_into_messages(full_group_prompt, DEFAULT_GROUP_SYSTEM)
        group_model = self.config.group_model
        max_completion_tokens = self.config.max_completion_tokens

        adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
            content=user_prompt,
            system_message=system_message,
            model_name=group_model,
            max_completion_tokens=max_completion_tokens,
        )
        if adjust_metadata.get("truncated"):
            logger.warning(
                f"Audience room {audience_room_id}: Group summary prompt truncated "
                f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
                f"to fit {group_model} context window"
            )

        group_result = await ai_gateway.call_via_gateway(
            context_id=audience_room_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": adjusted_user_prompt},
            ],
            max_tokens=max_completion_tokens,
            model=None,
            default_model=self.config.group_model,
            fallback_models=["openai/gpt-5-mini", "openai/gpt-4o-mini"],
            config_default_attr="group_summary_default",
            config_fallbacks_attr="group_summary_fallbacks",
            hardcoded_default="anthropic/claude-sonnet-4.5",
            validate_summary=False,
            return_text=True,
            direct_api_fallback_model=self.config.group_model_snapshot,
        )
        group_summary = (group_result if isinstance(group_result, str) else str(group_result)).strip()

        from prompts import traits_generation_prompt
        full_traits_prompt = traits_generation_prompt.format(
            total_profiles=len(profile_summaries),
            combined_summaries=combined_summaries,
        )
        traits_system_message, traits_prompt = split_prompt_into_messages(
            full_traits_prompt, DEFAULT_TRAITS_SYSTEM
        )
        traits_max_completion_tokens = self.config.traits_max_completion_tokens
        adjusted_traits_prompt, traits_adjust_metadata = context_manager.adjust_content_to_fit_context_window(
            content=traits_prompt,
            system_message=traits_system_message,
            model_name=group_model,
            max_completion_tokens=traits_max_completion_tokens,
        )
        if traits_adjust_metadata.get("truncated"):
            logger.warning(
                f"Audience room {audience_room_id}: Traits prompt truncated "
                f"({traits_adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
                f"to fit {group_model} context window"
            )

        traits_result = await ai_gateway.call_via_gateway(
            context_id=audience_room_id,
            messages=[
                {"role": "system", "content": traits_system_message},
                {"role": "user", "content": adjusted_traits_prompt},
            ],
            max_tokens=traits_max_completion_tokens,
            model=None,
            default_model=self.config.group_model,
            fallback_models=["openai/gpt-5-mini", "openai/gpt-4o-mini"],
            config_default_attr="group_summary_default",
            config_fallbacks_attr="group_summary_fallbacks",
            hardcoded_default="anthropic/claude-sonnet-4.5",
            validate_summary=False,
            return_text=True,
            direct_api_fallback_model=self.config.group_model_snapshot,
        )
        traits_response = (traits_result if isinstance(traits_result, str) else str(traits_result)).strip()
        if "```json" in traits_response:
            traits_response = traits_response.split("```json")[1].split("```")[0].strip()
        elif "```" in traits_response:
            traits_response = traits_response.split("```")[1].split("```")[0].strip()

        try:
            traits_data = json.loads(traits_response)
            self._validate_traits_response(traits_data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse traits JSON: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to parse traits JSON: {str(e)}")
        except ValueError as e:
            logger.error(f"Invalid traits structure: {e}")
            raise HTTPException(status_code=500, detail=f"Invalid traits structure: {str(e)}")

        logger.info(f"Successfully generated traits for audience room {audience_room_id}")

        room_description_data["summary"] = group_summary
        room_description_data["traits"] = traits_data["traits"]
        updated_description_url = upload_json_to_s3(description_key, room_description_data)
        database.update_audience_room(
            audience_room_id,
            {"descriptionS3Url": updated_description_url},
            enterprise_name=enterprise_name,
        )

        return {
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "summary": group_summary,
            "traits": traits_data["traits"],
            "total_profiles": len(profiles),
            "profiles_processed": profiles_processed,
            "profiles_skipped": profiles_skipped,
            "companies_represented": list(companies),
            "description_s3_url": updated_description_url,
        }
