from typing import Dict, Any, List, Tuple, Optional
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_core.messages import trim_messages

from src.agents.langgraph.core.graph_nodes.base_node import BaseNode
from src.agents.langgraph.core.tracing import trace_node
from src.agents.langgraph.core.state import AgentState
from src.agents.langgraph.core.models import NodeType
from src.agents.langgraph.core.graph_nodes.utils.llm_utils import get_llm_from_state, invoke_llm_with_retry, add_model_metadata_to_trace
from src.agents.langgraph.core.prompts import (
    pm_response_generation_prompt,
    product_story_response_prompt,
    page_context_response_prompt,
    general_chat_prompt,
    profile_search_response_prompt,
    user_journey_prompt
)
from src.agents.langgraph.core.graph_nodes.models.context_info import ContextInfo
from src.agents.langgraph.core.graph_nodes.utils.formatting_utils import fix_markdown_spacing
from src.agents.langgraph.core.graph_nodes.utils.nba_utils import generate_nba_response
from src.agents.langgraph.utils.format_citations import make_citations_clickable
from src.config.logging_config import logger

class GenerateResponseNode(BaseNode):
    """Node responsible for generating responses using LLM with proper message handling."""
    
    def __init__(self) -> None:
        """Initialize the GenerateResponseNode with configuration constants."""
        super().__init__()
        self.CONFIDENCE_THRESHOLD = 0.6
        self.MAX_TRIMMED_TOKENS = 3000
        self.MAX_NBA_MESSAGES = 6
        self.GENERAL_GREETINGS = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hi there"]
        self.DATA_INSUFFICIENCY_INDICATORS = [
            "don't have enough relevant data",
            "don't have sufficient data", 
            "don't have enough information",
            "additional data required",
            "please upload more data",
            "need more specific data",
            "unable to provide a complete answer",
            "consider uploading",
            "more data would be needed",
            "lack the necessary data"
        ]
    
    @trace_node("generate_response")
    def run(self, state: AgentState) -> Dict[str, Any]:
        """Main entry point for generating responses using LLM."""
        try:
            # Handle clone-specific error cases first
            query_type = state.get("query_type")
            if query_type == "error":
                error_message = state.get("error_message", "An error occurred while processing your request.")
                logger.info(f"Handling clone error: {error_message}")
                raise Exception(error_message)
            
            elif query_type == "clone_not_ready":
                response_message = state.get("response_message", "The clone is not ready yet.")
                logger.info(f"Handling clone not ready: {response_message}")
                raise Exception(response_message)
            
            llm = get_llm_from_state(state, NodeType.GENERATE_RESPONSE)
            messages = state.get("messages", [])
            latest_message = messages[-1].content if messages else ""
            
            nba_name = state.get("nba_name")
            if nba_name:
                rag_enabled = state.get("rag_enabled", True)
                logger.info(f"NBA mode detected: {nba_name} with RAG {'enabled' if rag_enabled else 'disabled'}")
                return generate_nba_response(state, llm, messages, latest_message, nba_name, self.MAX_NBA_MESSAGES)
            
            context_info = self._extract_context_information(state, messages)
            
            if self._is_general_greeting(context_info, messages):
                return self._create_greeting_response(state, messages, context_info.file_sources)
            
            rag_results_text = self._process_rag_results(context_info.rag_results)
            file_sources_str = self._format_file_sources(context_info.file_sources, context_info.rag_results)
            
            trimmed_messages = self._prepare_messages_for_llm(messages, llm)
            
            system_content, base_confidence = self._generate_system_prompt(
                context_info, rag_results_text, latest_message, file_sources_str
            )
            
            response_text = self._generate_llm_response(llm, system_content, trimmed_messages, latest_message, state)
            
            confidence_score, upload_more_data, upload_reason = self._analyze_response_quality(
                response_text, context_info, base_confidence
            )
            
            response_text = self._post_process_response(response_text, context_info.rag_results, 
                                                     context_info.file_sources, context_info.search_type)
            
            return self._build_response_result(
                state, messages, response_text, context_info.file_sources,
                upload_more_data, confidence_score
            )
            
        except Exception as e:
            logger.error(f"Error in generate_response: {str(e)}", exc_info=True)
            raise
    
    def _extract_context_information(self, state: AgentState, messages: List) -> ContextInfo:
        """Extract and organize context information from state and messages."""
        
        # Extract basic context
        rag_results = state.get("rag_results", [])
        file_sources = state.get("file_sources", [])
        search_type = state.get("query_type", "general_chat")
        search_query = state.get("search_query", "")
        enterprise_name = state.get("enterprise_name", "")
        
        # Extract S3 profile summary (populated by retrieve_data_node for profile searches)
        profile_summary_from_s3 = state.get("profile_summary_from_s3")
        
        # Log profile search specific info
        if search_type == "profile_search":
            logger.info("--- Context Extraction for Profile Search ---")
            logger.info(f"RAG results count: {len(rag_results)}")
            logger.info(f"File sources count: {len(file_sources)}")
            logger.info(f"Search type: {search_type}")
            profile_id = state.get("profile_id", "unknown")
            logger.info(f"Profile ID from state: {profile_id}")
            logger.info(f"Profile summary from S3 available: {profile_summary_from_s3 is not None}")
            if profile_summary_from_s3:
                logger.info(f"Profile summary from S3 length: {len(profile_summary_from_s3)} chars")
        
        has_product_story_context = self._has_product_story_context(messages)
        has_page_context = self._has_page_context(messages)
        
        logger.info(f"Conversation has product story context: {has_product_story_context}")
        logger.info(f"Conversation has page context: {has_page_context}")
        
        extracted_product_story = self._extract_product_story(messages) if has_product_story_context else ""
        extracted_page_context = self._extract_page_context(messages) if has_page_context else ""
        
        context_type = self._determine_context_type(state, has_product_story_context, has_page_context)
        
        is_general_chat = self._is_general_chat_mode(
            search_type, has_product_story_context, has_page_context, state
        )
        
        upload_more_data, confidence_score, upload_reason = self._initialize_upload_flags(
            search_type, rag_results
        )
        
        return ContextInfo(
            rag_results=rag_results,
            file_sources=file_sources,
            search_type=search_type,
            search_query=search_query,
            enterprise_name=enterprise_name,
            has_product_story_context=has_product_story_context,
            has_page_context=has_page_context,
            extracted_product_story=extracted_product_story,
            extracted_page_context=extracted_page_context,
            context_type=context_type,
            is_general_chat=is_general_chat,
            upload_more_data=upload_more_data,
            confidence_score=confidence_score,
            upload_reason=upload_reason,
            profile_summary_from_s3=profile_summary_from_s3
        )
    
    def _has_product_story_context(self, messages: List) -> bool:
        """Check if the first message contains product story context."""
        return (
            len(messages) > 0 and 
            isinstance(messages[0], HumanMessage) and
            messages[0].content.startswith("[PRODUCT STORY CONTEXT]")
        )
    
    def _has_page_context(self, messages: List) -> bool:
        """Check if the first message contains page context."""
        return (
            len(messages) > 0 and 
            isinstance(messages[0], HumanMessage) and
            messages[0].content.startswith("[PAGE CONTEXT]")
        )
    
    def _extract_product_story(self, messages: List) -> str:
        """Extract product story content from the first message."""
        if not messages or not isinstance(messages[0], HumanMessage):
            return ""
        
        content = messages[0].content
        if content.startswith("[PRODUCT STORY CONTEXT]"):
            start_idx = len("[PRODUCT STORY CONTEXT]")
            end_idx = content.find("[END PRODUCT STORY CONTEXT]")
            if end_idx != -1:
                extracted = content[start_idx:end_idx].strip()
                logger.info(f"Extracted product story from first message: {len(extracted)} chars")
                return extracted
        
        return ""
    
    def _extract_page_context(self, messages: List) -> str:
        """Extract page context content from the first message."""
        if not messages or not isinstance(messages[0], HumanMessage):
            return ""
        
        content = messages[0].content
        if content.startswith("[PAGE CONTEXT]"):
            start_idx = len("[PAGE CONTEXT]")
            end_idx = content.find("[END PAGE CONTEXT]")
            if end_idx != -1:
                extracted = content[start_idx:end_idx].strip()
                logger.info(f"Extracted page context from first message: {len(extracted)} chars")
                return extracted
        
        return ""
    
    def _determine_context_type(self, state: AgentState, has_product_story_context: bool, 
                               has_page_context: bool) -> Optional[str]:
        """Determine the type of context for response generation."""
        # Check for profile search mode first
        query_type = state.get("query_type")
        if query_type == "profile_search":
            profile_id = state.get("profile_id", "unknown")
            logger.info("=" * 60)
            logger.info("PROFILE SEARCH MODE DETECTED")
            logger.info(f"Profile ID: {profile_id}")
            logger.info(f"Query Type: {query_type}")
            logger.info("Will use profile_search_response_prompt from LangSmith")
            logger.info("=" * 60)
            return "profile_search"
        
        # Check for user_journey prompt definition
        if query_type == "user_journey":
            logger.info("=" * 60)
            logger.info("USER JOURNEY MODE DETECTED")
            logger.info(f"Query Type: {query_type}")
            logger.info("Will use user_journey_prompt from LangSmith")
            logger.info("=" * 60)
            return "user_journey"
        
        if has_product_story_context or state.get("product_story"):
            logger.info("Using product story context for response generation")
            return "product_story"
        elif has_page_context or state.get("page_context"):
            logger.info("Using page context for response generation")
            return "page_context"
        
        return None
    
    def _is_general_chat_mode(self, search_type: str, has_product_story_context: bool,
                             has_page_context: bool, state: AgentState) -> bool:
        """Check if this is a general chat interaction."""
        return (search_type == "general_chat" and 
                not (has_product_story_context or state.get("product_story") or 
                     has_page_context or state.get("page_context")))
    
    def _initialize_upload_flags(self, search_type: str, rag_results: List) -> Tuple[bool, float, str]:
        """Initialize upload flags based on search type and RAG results."""
        upload_more_data = False
        confidence_score = 0.0
        upload_reason = ""
        
        if search_type in ["enterprise", "combined"] and (not rag_results or len(rag_results) == 0):
            upload_more_data = True
            confidence_score = 0.2
            upload_reason = "No relevant enterprise data found"
            logger.info("Setting upload_more_data=True due to no RAG results found")
        
        return upload_more_data, confidence_score, upload_reason
    
    def _is_general_greeting(self, context_info, messages: List) -> bool:
        """Check if this is a general greeting that can use the fast path."""
        return (context_info.is_general_chat and messages and
                messages[-1].content.lower().strip() in self.GENERAL_GREETINGS)
    
    def _create_greeting_response(self, state: AgentState, messages: List, file_sources: List) -> Dict[str, Any]:
        """Create a fast response for general greetings."""
        logger.info("Using fast path for general greeting")
        greeting_response = "Hello! I'm your Product and UX Research assistant. How can I help you today?"
        
        return {
            "messages": messages + [AIMessage(content=greeting_response)],
            "response": greeting_response,
            "file_sources": file_sources,
            "thoughts": state.get("thoughts", []) + ["Using existing AI response"],
            "next_best_actions": state.get("next_best_actions", {}),
            "upload_more_data": False,
            "confidence_score": 0.95
        }
    
    def _process_rag_results(self, rag_results: List) -> str:
        """Process RAG results into formatted text."""
        if isinstance(rag_results, list) and rag_results:
            rag_results_chunks = []
            for doc in rag_results:
                if hasattr(doc, 'page_content'):
                    source_type = doc.metadata.get('file_source', 'Unknown') if hasattr(doc, 'metadata') else 'Unknown'
                    rag_results_chunks.append(f"Source Type: {source_type}\n{doc.page_content}\n")
            
            rag_results_text = "\n---\n".join(rag_results_chunks)
            logger.info(f"Total RAG results text length: {len(rag_results_text)} characters")
            return rag_results_text
        else:
            logger.warning("No RAG results to process")
            return str(rag_results)
    
    def _extract_profile_summary(self, rag_results: List) -> str:
        """Extract ALL unique profile summaries from RAG results metadata for profile search."""
        logger.info("=" * 80)
        logger.info("EXTRACTING PROFILE SUMMARIES FROM RAG RESULTS")
        logger.info("=" * 80)
        
        if not isinstance(rag_results, list) or not rag_results:
            logger.warning("❌ No RAG results available to extract profile summary")
            return "Profile summary not available"
        
        logger.info(f"Total RAG results documents: {len(rag_results)}")
        
        # Collect ALL unique summaries (keyed by summary text to avoid duplicates)
        unique_summaries = {}
        
        for idx, doc in enumerate(rag_results):
            if hasattr(doc, 'metadata'):
                summary = doc.metadata.get('summary', '')
                profile_id = doc.metadata.get('profile_id', 'unknown')
                company_type = doc.metadata.get('company_type', 'unknown')
                
                if summary and summary not in unique_summaries:
                    unique_summaries[summary] = {
                        'profile_id': profile_id,
                        'company_type': company_type,
                        'first_seen_idx': idx + 1
                    }
        
        if not unique_summaries:
            logger.warning("❌ No profile summaries found in any RAG results metadata")
            logger.warning("Returning fallback message")
            logger.info("=" * 80)
            return "Profile summary not available"
        
        logger.info(f"✅ Found {len(unique_summaries)} unique profile summary(ies):")
        
        # Format all summaries
        all_summaries = []
        for summary_text, info in unique_summaries.items():
            logger.info(f"  Profile {info['profile_id']} ({info['company_type']}) - first seen at doc {info['first_seen_idx']}")
            logger.info(f"    Summary preview: {summary_text[:150]}...")
            all_summaries.append(summary_text)
        
        # Combine all summaries with clear separation
        combined_summary = "\n\n---PROFILE SEPARATOR---\n\n".join(all_summaries)
        
        logger.info(f"Total combined profile summary length: {len(combined_summary)} characters")
        logger.info(f"Sending {len(unique_summaries)} profile summary(ies) to prompt placeholder")
        logger.info("=" * 80)
        
        return combined_summary
    
    def _format_file_sources(self, file_sources: List, rag_results: List) -> str:
        """Format file sources into a string representation."""
        formatted_file_sources = []
        
        for source in file_sources:
            if isinstance(source, dict) and "uri" in source:
                formatted_file_sources.append(f"{source.get('filename', 'unknown')}:{source['uri']}")
            elif isinstance(source, str):
                formatted_file_sources.append(source)
        
        # Add formatted_title for Coda documents
        for doc in rag_results:
            if (hasattr(doc, 'metadata') and 
                doc.metadata.get('file_source') == 'Coda' and 
                'formatted_title' in doc.metadata):
                file_source_str = f"Coda: {doc.metadata['formatted_title']}"
                if file_source_str not in formatted_file_sources:
                    formatted_file_sources.append(file_source_str)
        
        return "\n".join(formatted_file_sources)
    
    def _prepare_messages_for_llm(self, messages: List, llm) -> List:
        """Filter and trim messages for LLM processing."""
        filtered_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue
                
            if isinstance(msg, HumanMessage):
                msg_content = msg.content
                if "[PRODUCT STORY CONTEXT]" in msg_content:
                    logger.info("Filtered out product story context message")
                    continue
                if "[PAGE CONTEXT]" in msg_content:
                    logger.info("Filtered out page context message")
                    continue
                    
            filtered_messages.append(msg)
        
        trimmed_messages = trim_messages(
            filtered_messages,
            max_tokens=self.MAX_TRIMMED_TOKENS,
            strategy="last",
            token_counter=llm,
            include_system=False,
            allow_partial=False,
            start_on="human"
        )
        
        logger.info(f"Input messages count: {len(messages)}")
        logger.info(f"Filtered messages count: {len(filtered_messages)}")
        logger.info(f"Trimmed messages count: {len(trimmed_messages)}")
        
        # CRITICAL: Claude API requires at least one message. If trim_messages returned empty
        # (because a single message exceeds MAX_TRIMMED_TOKENS), use the last filtered message.
        # This can happen with very long user inputs like persona role-play prompts.
        if len(trimmed_messages) == 0 and len(filtered_messages) > 0:
            logger.warning(f"trim_messages returned empty list - message likely exceeds {self.MAX_TRIMMED_TOKENS} tokens")
            logger.warning("Using last filtered message to ensure Claude API receives at least one message")
            trimmed_messages = [filtered_messages[-1]]
        
        return trimmed_messages
    
    def _generate_system_prompt(self, context_info, rag_results_text: str, 
                               latest_message: str, file_sources_str: str) -> Tuple[str, float]:
        """Generate the appropriate system prompt based on context type."""
        if context_info.context_type == "profile_search":
            logger.info("=" * 60)
            logger.info("GENERATING PROFILE SEARCH SYSTEM PROMPT")
            logger.info("=" * 60)
            
            # Use S3 profile summary if available, otherwise fall back to extracting from metadata
            if context_info.profile_summary_from_s3:
                profile_summary = context_info.profile_summary_from_s3
                logger.info(f"✅ Using profile summary from S3 ({len(profile_summary)} chars)")
            else:
                logger.warning("⚠️ S3 profile summary not available, falling back to metadata extraction")
                profile_summary = self._extract_profile_summary(context_info.rag_results)
            
            logger.info("Fetching profile_search_response_prompt from LangSmith...")
            logger.info(f"Filling placeholders:")
            logger.info(f"  - profile_summary: {len(profile_summary)} chars (source: {'S3' if context_info.profile_summary_from_s3 else 'metadata'})")
            logger.info(f"  - rag_results: {len(rag_results_text)} chars")
            logger.info(f"  - latest_message: '{latest_message[:100]}...'")
            logger.info(f"  - conversation_history: (empty)")
            
            system_content = profile_search_response_prompt.format(
                profile_summary=profile_summary,
                rag_results=rag_results_text,
                latest_message=latest_message,
                conversation_history=" "
            )
            
            logger.info(f"✅ Profile search prompt formatted successfully")
            logger.info(f"Final system prompt length: {len(system_content)} characters")
            logger.info(f"Confidence score: 0.8")
            logger.info("=" * 60)
            
            return system_content, 0.8
        
        elif context_info.context_type == "user_journey":
            logger.info("=" * 60)
            logger.info("GENERATING USER JOURNEY SYSTEM PROMPT")
            logger.info("=" * 60)
            
            # Use profile_summary as persona_description (same as profile_search)
            if context_info.profile_summary_from_s3:
                persona_description = context_info.profile_summary_from_s3
                logger.info(f"✅ Using profile summary from S3 as persona description ({len(persona_description)} chars)")
            else:
                logger.warning("⚠️ S3 profile summary not available, falling back to metadata extraction")
                persona_description = self._extract_profile_summary(context_info.rag_results)
            
            logger.info("Fetching user_journey_prompt from LangSmith...")
            logger.info(f"Filling placeholders:")
            logger.info(f"  - persona_description: {len(persona_description)} chars")
            logger.info(f"  - rag_results: {len(rag_results_text)} chars")
            logger.info(f"  - latest_message: '{latest_message[:100]}...'")
            
            system_content = user_journey_prompt.format(
                persona_description=persona_description,
                rag_results=rag_results_text,
                latest_message=latest_message,
                conversation_history=" "
            )
            
            logger.info(f"✅ User journey prompt formatted successfully")
            logger.info(f"Final system prompt length: {len(system_content)} characters")
            logger.info(f"Confidence score: 0.85")
            logger.info("=" * 60)
            
            return system_content, 0.85
        
        elif context_info.context_type == "product_story":
            product_story = context_info.extracted_product_story or ""
            system_content = product_story_response_prompt.format(
                product_story=product_story,
                rag_results=rag_results_text,
                latest_message=latest_message,
                enterprise_name=context_info.enterprise_name,
                conversation_history=" "
            )
            logger.info("Using product story response prompt")
            return system_content, 0.8
        
        elif context_info.context_type == "page_context":
            page_context = context_info.extracted_page_context or ""
            system_content = page_context_response_prompt.format(
                page_context=page_context,
                rag_results=rag_results_text,
                latest_message=latest_message,
                enterprise_name=context_info.enterprise_name,
                conversation_history=" "
            )
            logger.info("Using page context response prompt")
            return system_content, 0.8
        
        elif context_info.is_general_chat:
            system_content = general_chat_prompt.format(
                enterprise_name=context_info.enterprise_name
            )
            logger.info("Using general chat approach - no external data retrieval")
            return system_content, 0.75
        
        else:
            system_content = self._create_pm_response_prompt(
                rag_results_text, latest_message, context_info, file_sources_str
            )
            logger.info("Using PM response generation approach")
            return system_content, 0.0 
    
    def _create_pm_response_prompt(self, rag_results_text: str, latest_message: str,
                                  context_info, file_sources_str: str) -> str:
        """Create a PM response generation system prompt."""
        system_content = pm_response_generation_prompt.format(
            rag_results=rag_results_text,
            conversation_history=" ",
            latest_message=latest_message,
            search_type=context_info.search_type,
            search_query=context_info.search_query,
            file_sources=file_sources_str,
            enterprise_name=context_info.enterprise_name
        )
        
        system_content += """

ADDITIONAL INSTRUCTIONS:
After generating your response, analyze:
1. Data relevance: How relevant was the enterprise data to the query (0-100%)
2. Data sufficiency: Was there enough data to fully answer the query (yes/no)
3. Data quality: How high quality and trustworthy was the data (0-100%)

At the end of your reasoning process (but not in the final response), add:
[DATA_ANALYSIS]
relevance: <0-100>
sufficiency: <yes/no>
quality: <0-100>
confidence_score: <0.0-1.0>
[/DATA_ANALYSIS]

Base confidence_score on the combination of relevance, sufficiency, and quality.
DO NOT include this analysis in your client-facing response.
"""
        return system_content
    
    def _generate_llm_response(self, llm, system_content: str, trimmed_messages: List, 
                              latest_message: str, state: AgentState = None) -> str:
        """Generate response using LLM with proper error handling and retry logic."""
        chat_messages = [SystemMessage(content=system_content), *trimmed_messages]
        
        logger.info(f"Using {len(chat_messages)} structured chat messages")
        logger.info(f"System message is first: {isinstance(chat_messages[0], SystemMessage)}")
        
        self._log_token_usage(system_content, trimmed_messages)
        
        # Add model metadata for response generation
        add_model_metadata_to_trace(
            llm,
            operation_type="response_generation",
            extra_metadata={
                "message_count": len(chat_messages),
                "system_prompt_length": len(system_content),
                "node_type": NodeType.GENERATE_RESPONSE.value
            }
        )
        
        # Use retry logic for LLM invocation - let exceptions bubble up
        response = invoke_llm_with_retry(llm, chat_messages, state or {}, NodeType.GENERATE_RESPONSE)
        
        if not hasattr(response, "content") or not response.content:
            logger.error("LLM returned an empty response")
            raise Exception("LLM returned empty response")
        
        if hasattr(response, "content"):
            response_text = response.content
            logger.info(f"Successfully extracted response content of length: {len(response_text)}")
        else:
            response_text = str(response)
            logger.info(f"Used string representation of response: {len(response_text)}")
        
        return response_text
    
    def _log_token_usage(self, system_content: str, trimmed_messages: List) -> None:
        """Log token usage for debugging purposes."""
        from src.agents.langgraph.context_window import TokenCounterFactory
        token_counter = TokenCounterFactory.create_counter("claude-3.5-sonnet")
        
        system_tokens = token_counter.count_tokens(system_content)
        conversation_tokens = sum(token_counter.count_tokens(msg.content) for msg in trimmed_messages)
        total_input_tokens = system_tokens + conversation_tokens
        
        logger.info(f"🔥 Sending {total_input_tokens:,} tokens to Claude API")
    
    
    def _analyze_response_quality(self, response_text: str, context_info, 
                                 base_confidence: float) -> Tuple[float, bool, str]:
        """Analyze response quality and determine confidence score and upload flags."""
        confidence_score = base_confidence
        upload_more_data = context_info.upload_more_data
        upload_reason = context_info.upload_reason
        
        confidence_score = self._extract_data_analysis(response_text, confidence_score)
        
        if context_info.context_type in ["product_story", "page_context"] and confidence_score < 0.7:
            confidence_score = 0.7
        
        if (context_info.search_type in ["enterprise", "combined"] and not upload_more_data):
            if self._has_data_insufficiency_indicators(response_text):
                upload_more_data = True
                upload_reason = "LLM indicated insufficient data"
                if confidence_score > 0.6:
                    confidence_score = 0.6
        
        if (context_info.search_type in ["enterprise", "combined"] and 
            confidence_score <= self.CONFIDENCE_THRESHOLD):
            upload_more_data = True
            logger.info(f"Setting upload_more_data=True due to low confidence: {confidence_score} < {self.CONFIDENCE_THRESHOLD}")
        elif confidence_score >= self.CONFIDENCE_THRESHOLD:
            upload_more_data = False
            logger.info(f"Setting upload_more_data=False due to high confidence: {confidence_score} >= {self.CONFIDENCE_THRESHOLD}")
        
        return confidence_score, upload_more_data, upload_reason
    
    def _extract_data_analysis(self, response_text: str, default_confidence: float) -> float:
        """Extract data analysis from response text and calculate confidence score."""
        if "[DATA_ANALYSIS]" not in response_text:
            return default_confidence
        
        analysis_start = response_text.find("[DATA_ANALYSIS]")
        analysis_end = response_text.find("[/DATA_ANALYSIS]")
        if analysis_start < 0 or analysis_end <= analysis_start:
            return default_confidence
        
        analysis_text = response_text[analysis_start + 15:analysis_end].strip()
        relevance = 0.5
        quality = 0.5
        sufficiency = True
        confidence_score = default_confidence
        
        for line in analysis_text.split('\n'):
            if line.startswith('relevance:'):
                try:
                    relevance = float(line.split(':')[1].strip()) / 100.0
                except:
                    relevance = 0.5
            elif line.startswith('sufficiency:'):
                sufficiency = line.split(':')[1].strip().lower() == 'yes'
            elif line.startswith('quality:'):
                try:
                    quality = float(line.split(':')[1].strip()) / 100.0
                except:
                    quality = 0.5
            elif line.startswith('confidence_score:'):
                try:
                    confidence_score = float(line.split(':')[1].strip())
                except:
                    confidence_score = 0.5
        
        # Calculate confidence if not explicitly provided
        if confidence_score == default_confidence and confidence_score == 0.0:
            confidence_score = (relevance + quality) / 2.0
            if not sufficiency:
                confidence_score *= 0.7
        
        return confidence_score
    
    def _has_data_insufficiency_indicators(self, response_text: str) -> bool:
        """Check if response contains indicators of insufficient data."""
        return any(indicator.lower() in response_text.lower() 
                  for indicator in self.DATA_INSUFFICIENCY_INDICATORS)
    
    def _post_process_response(self, response_text: str, rag_results: List, 
                              file_sources: List, search_type: str) -> str:
        """Post-process the response text with formatting and citations."""
        if not response_text or len(response_text.strip()) == 0:
            logger.error("Empty response text received from LLM")
            return "I apologize, but I couldn't generate a proper response. Please try again."
        
        if "[DATA_ANALYSIS]" in response_text:
            analysis_start = response_text.find("[DATA_ANALYSIS]")
            response_text = response_text[:analysis_start].strip()
        
        # Convert citations to clickable markdown links only for Posts (profile searches)
        # Check if any rag_results have file_source == "Posts"
        has_posts = False
        if rag_results:
            for doc in rag_results:
                # Check both metadata attribute and direct access
                if hasattr(doc, 'metadata'):
                    metadata = doc.metadata if hasattr(doc, 'metadata') else {}
                    if metadata.get("file_source") == "Posts":
                        has_posts = True
                        break
                # Also check file_sources for Posts indicators
                elif isinstance(doc, dict) and doc.get("file_source") == "Posts":
                    has_posts = True
                    break
        
        # Also check file_sources list for Posts
        if not has_posts and file_sources:
            for source in file_sources:
                if "Posts" in str(source) or "profile_" in str(source):
                    has_posts = True
                    break
        
        if has_posts and rag_results:
            logger.info(f"Processing citations for Posts - found {len(rag_results)} RAG results")
            response_text = make_citations_clickable(response_text, rag_results, file_sources)
        
        if "```" in response_text or "#" in response_text:
            response_text = fix_markdown_spacing(response_text)
        
        return response_text
    
    def _build_response_result(self, state: AgentState, messages: List, response_text: str,
                              file_sources: List, upload_more_data: bool, confidence_score: float) -> Dict[str, Any]:
        """Build the final response result dictionary."""
        response_message = AIMessage(content=response_text)
        
        logger.info(f"Upload more data: {upload_more_data}")
        logger.info(f"Confidence score: {confidence_score}")
        
        return {
            "messages": messages + [response_message],
            "response": response_text,
            "file_sources": file_sources,
            "thoughts": state.get("thoughts", []),
            "next_best_actions": state.get("next_best_actions", {}),
            "upload_more_data": upload_more_data,
            "confidence_score": confidence_score
        }
    