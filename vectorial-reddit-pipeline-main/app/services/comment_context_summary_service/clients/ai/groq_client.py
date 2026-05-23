"""Groq implementation of ContextSummaryClientInterface via AI Gateway."""
from typing import Optional, Any, Dict
from fastapi import HTTPException
from app.config import logger
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.ai_gateway_service import ai_gateway
from .interface import ContextSummaryClientInterface


class GroqContextSummaryClient(ContextSummaryClientInterface):
    """Groq implementation for summarizing comment context via AI Gateway."""

    def __init__(self, config: Optional[CommentContextSummaryConfig] = None):
        self.config = config or CommentContextSummaryConfig()
        self._initialize()

    def _initialize(self) -> None:
        """Initialize and check if AI Gateway is configured."""
        if ai_gateway.enabled:
            logger.info("AI Gateway enabled for comment context summary service")
        else:
            logger.warning("AI Gateway not enabled - will use direct API fallback if configured")

    def is_configured(self) -> bool:
        """Check if AI Gateway is enabled or direct API fallback is available."""
        # Gateway is configured if enabled, or if direct API fallback is available
        return ai_gateway.enabled or bool(ai_gateway.config.api_key)

    def get_raw_client(self) -> Any:
        """Return the AI Gateway client instance."""
        return ai_gateway

    async def summarize_comment_context(
        self,
        context: Dict[str, Optional[str]]
    ) -> str:
        """Summarize comment context using AI Gateway (Groq with fallbacks)."""
        if not self.is_configured():
            raise HTTPException(
                status_code=503,
                detail="AI Gateway not configured. Set AI_GATEWAY_API_KEY or configure gateway."
            )
        
        post_title = context.get("post_title", "")
        post_body = context.get("post_body", "")
        parent_comment_body = context.get("parent_comment_body")
        
        prompt = f"""Create a 1-2 sentence summary of this Reddit discussion context. Include the main topic, the question being discussed, and what the user is responding to. Do not include any other information.

Post: {post_title} - {post_body}

Parent Comment: {parent_comment_body if parent_comment_body else "Responding directly to the post"}

Summary:"""
        
        # Format model name for gateway (add groq/ prefix if needed)
        model = self.config.groq_model
        if not model.startswith("groq/"):
            model = f"groq/{model}"
        
        try:
            # Use AI Gateway with return_text=True for simple text responses
            summary = await ai_gateway.call_via_gateway(
                context_id="comment_context_summary",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                model=model,
                config_default_attr="comment_context_summary_default",
                config_fallbacks_attr="comment_context_summary_fallbacks",
                hardcoded_default="groq/llama-3.3-70b-versatile",
                return_text=True,  # Returns string directly
                direct_api_fallback_model="openai/gpt-4o-mini"
            )
            
            if not summary or not isinstance(summary, str):
                raise ValueError("Gateway returned invalid summary")
            
            return summary.strip()
            
        except Exception as e:
            logger.error(f"Error calling AI Gateway for context summary: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate context summary: {str(e)}"
            )
