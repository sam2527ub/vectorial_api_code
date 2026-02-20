# Modular Application Structure

This directory contains the refactored modular structure for the Audience Workflow API.

## Directory Structure

```
app/
├── __init__.py
├── main.py              # FastAPI app entry point
├── config.py            # Configuration and client initialization
├── models/              # Pydantic request/response schemas
│   ├── __init__.py
│   └── schemas.py
├── utils/               # Utility functions
│   ├── __init__.py
│   ├── helpers.py       # General helper functions
│   └── s3_utils.py      # S3 operations
├── services/            # Business logic (one folder per feature)
│   ├── __init__.py
│   ├── user_profile_fetch_service/   # Profile fetch (Apify, enrichment)
│   ├── user_observation_fetch_service/  # Posts scrape, profile processor
│   ├── user_profile_summarization_service/
│   ├── openai_service/  # OpenAI/Claude calls, direct API fallback
│   └── ...
└── api/                 # API endpoints
    ├── __init__.py
    └── v1/              # API v1 routes
        ├── __init__.py
        ├── router.py    # Main router
        └── endpoints_legacy.py  # Bridge to legacy code
```

## Migration Status

**Current Status**: Modular structure created, app imports from `main_old.py` for backward compatibility.

**Completed**:
- ✅ Configuration extracted to `app/config.py`
- ✅ Pydantic models extracted to `app/models/schemas.py`
- ✅ Utility functions extracted to `app/utils/`
- ✅ One service folder per feature (e.g. user_profile_fetch_service, openai_service)
- ✅ Directory structure created

**Next Steps** (Incremental Migration):
1. Move endpoint functions from `main_old.py` to `app/api/v1/endpoints/*.py`
2. Move remaining service logic to `app/services/*.py`
3. Update imports in endpoint files
4. Remove legacy bridge once migration is complete

## Usage

The app can be run as before:
```bash
python main.py
# or
uvicorn main:app
```

The entry point (`main.py`) imports from `app/main.py`, which currently bridges to `main_old.py`.

## Benefits

- **Separation of Concerns**: Config, models, utils, services, and API routes are separated
- **Maintainability**: Easier to find and modify code
- **Testability**: Modules can be tested independently
- **Scalability**: Easy to add new features in organized modules
- **Industry Standard**: Follows FastAPI best practices

