# AI Gateway & Model Rotation Documentation

## Overview

The AI Gateway provides a unified interface for routing AI API requests (OpenAI, Anthropic, Groq) through a gateway service (e.g., Vercel AI Gateway) with automatic model rotation and fallback capabilities.

**Key Features:**
- Unified interface for multiple AI providers
- Automatic cross-provider model rotation
- Configurable fallbacks per use case
- Direct API fallback when gateway is disabled

---

## Architecture

**Core Components:**
- `AIGatewayClient` - Main entry point for all AI calls
- `UnifiedGatewayHandler` - Handles gateway requests
- `ModelRotation` - Manages fallback logic
- `DirectApiClient` - Fallback when gateway disabled
- `GatewayConfig` - Loads configuration from YAML/env vars

---

## Configuration

### Config File (`config.yaml`)

```yaml
gateway:
  enabled: true
  provider: vercel
  api_key: ""

model_rotation:
  profile_summary:
    default: "openai/gpt-5-mini"
    fallbacks: ["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"]
  
  group_summary:
    default: "anthropic/claude-sonnet-4.5"
    fallbacks: ["openai/gpt-5-mini", "openai/gpt-4o-mini"]
```

### Environment Variables

```bash
USE_AI_GATEWAY=true
AI_GATEWAY_API_KEY=your_key
AI_GATEWAY_BASE_URL=https://ai-gateway.vercel.sh/v1
```

**Priority:** Environment variables > YAML config > Hardcoded defaults

---

## Model Rotation

### How It Works

1. **Resolve primary model** from: explicit parameter → default → config → hardcoded
2. **Resolve fallbacks** from: parameter → config → empty list
3. **Normalize model names** (add provider prefix for Vercel: `gpt-5-mini` → `openai/gpt-5-mini`)
4. **Remove duplicates**
5. **Gateway tries models in order** on failure

### Model Normalization

Vercel gateway requires provider prefix:
- `gpt-5-mini` → `openai/gpt-5-mini`
- `claude-sonnet-4.5` → `anthropic/claude-sonnet-4.5`
- Already prefixed models remain unchanged

---

## Complete Workflow

```
1. Request → AIGatewayClient.call_via_gateway()
2. Check gateway enabled? → If no, use DirectApiClient
3. Resolve model config (default + fallbacks)
4. Normalize model names for provider
5. Send request to gateway with providerOptions
6. Gateway handles rotation automatically
7. Process response (JSON for OpenAI/Groq, text for Claude)
8. Return result or raise exception
```

**Error Handling:**
- **Transient errors**: SDK retries (2x) → Gateway tries fallbacks → Raise if all fail
- **Validation errors**: Logged and re-raised
- **Gateway disabled**: Falls back to direct API calls

---

## Usage Examples

### Basic Profile Summary (JSON)

```python
from app.services.ai_gateway import ai_gateway

result = await ai_gateway.call_via_gateway(
    context_id="user_123",
    messages=[{"role": "user", "content": "..."}],
    max_tokens=2000,
    config_default_attr='profile_summary_default',
    config_fallbacks_attr='profile_summary_fallbacks',
    return_text=False  # Returns JSON dict
)
```

### Basic Group Summary (Text)

```python
result = await ai_gateway.call_via_gateway(
    context_id="group_456",
    messages=[...],
    max_tokens=4000,
    config_default_attr='group_summary_default',
    config_fallbacks_attr='group_summary_fallbacks',
    return_text=True  # Returns plain text
)
```

### Using AIClient Wrapper

```python
from app.services.clients.ai_client.ai_client import AIClient

ai_client = AIClient(
    default_model="openai/gpt-5-mini",
    fallback_models=["anthropic/claude-sonnet-4.5"]
)

# JSON response
result = await ai_client.call_openai(profile_id, messages, max_tokens)

# Text response
result = await ai_client.call_claude(context_id, messages, max_tokens)
```

---

## API Reference

### `call_via_gateway()`

**Key Parameters:**
- `context_id` - Request identifier for logging
- `messages` - Conversation messages
- `max_tokens` - Max response tokens
- `model` - Explicit model (optional)
- `return_text` - `False` for JSON dict, `True` for text string
- `config_default_attr` - Config attribute for default model
- `config_fallbacks_attr` - Config attribute for fallbacks

**Returns:**
- `Dict[str, Any]` if `return_text=False`
- `str` if `return_text=True`

---

## Best Practices

1. **Use correct config attributes:**
   - Profile summaries: `profile_summary_*` with `return_text=False`
   - Group summaries: `group_summary_*` with `return_text=True`

2. **Always configure fallbacks** for reliability

3. **Use meaningful context IDs** for logging: `f"profile_{user_id}"`

4. **Enable validation** for critical responses: `validate_summary=True`

5. **Use `AIClient` wrapper** for service-specific defaults

---

## Troubleshooting

**Gateway not enabled:**
- Check `USE_AI_GATEWAY=true` and `AI_GATEWAY_API_KEY` are set

**No fallbacks:**
- Verify fallback models in `config.yaml` or passed as parameters

**Invalid model names:**
- Use format `provider/model-name` (e.g., `openai/gpt-5-mini`)
- Gateway auto-normalizes, but explicit format is preferred

---

This architecture provides reliable AI API calls with automatic failover and minimal configuration overhead.
