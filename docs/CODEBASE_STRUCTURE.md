# Codebase Structure Documentation

This document provides a comprehensive overview of the Audience Workflow codebase structure, explaining the purpose of each file and directory.

Audience-workflow/
│
├── 📁 ROOT FILES
│   ├── main.py                          # ⚡ Entry point wrapper - imports app from app/main.py (for deployment compatibility)
│   ├── requirements.txt                 # 📦 Python dependencies (FastAPI, psycopg2, OpenAI, etc.)
│   ├── package.json                     # 📦 Node.js package config (for Prisma generation on Vercel)
│   ├── vercel.json                      # 🚀 Vercel deployment configuration
│   ├── vercel_build_prisma.sh           # 🔧 Build script for Prisma client generation on Vercel
│   ├── deploy.sh                        # 🚀 Deployment script (Vercel CLI helper)
│   └── .gitignore                       # 🚫 Git ignore rules (excludes generated Prisma clients)
│
├── 📁 app/                              # 🎯 MAIN APPLICATION PACKAGE
│   │
│   ├── __init__.py                      # 📦 Package init - exports database module
│   │
│   ├── main.py                          # 🚀 FastAPI application setup
│   │                                    #    - Creates FastAPI app instance
│   │                                    #    - Configures CORS middleware
│   │                                    #    - Sets up lifespan (database connection checks)
│   │                                    #    - Includes API router
│   │                                    #    - Health check endpoint (/)
│   │
│   ├── config.py                        # ⚙️  Configuration & client initialization
│   │                                    #    - Environment variable loading
│   │                                    #    - Third-party client setup (PDL, Apify, OpenAI, Groq, S3)
│   │                                    #    - Database availability checks
│   │                                    #    - Logging configuration
│   │
│   ├── 📁 database/                     # 💾 DATABASE MODULE (replaces Prisma client)
│   │   ├── __init__.py                  # 📦 Exports all database functions and models
│   │   └── connection.py                # 🔌 Database connection & operations
│   │                                    #    - Connection pooling (main & audience DBs)
│   │                                    #    - Data models (ScrapeJob, AudienceRoom, etc.)
│   │                                    #    - CRUD operations for all tables
│   │                                    #    - Uses psycopg2 (avoids Prisma deployment issues)
│   │
│   ├── 📁 api/                          # 🌐 API ROUTES
│   │   └── v1/
│   │       ├── router.py                # 🔀 Main API router - registers all endpoint routers
│   │       └── endpoints/               # 📍 API ENDPOINT HANDLERS
│   │           ├── audience.py          # 👥 Audience room management
│   │           │                         #    - Create/delete audience rooms
│   │           │                         #    - Get profiles, posts, descriptions
│   │           │                         #    - Generate summaries (individual & group)
│   │           │
│   │           ├── classifier.py        # 🏷️  Post classification endpoints
│   │           │                         #    - Run classifier on all posts in a room
│   │           │                         #    - Run classifier for specific profiles
│   │           │                         #    - Uses Groq LLM for classification
│   │           │
│   │           ├── enrich.py            # ✨ Profile enrichment endpoints
│   │           │                         #    - Enrich profiles using PeopleDataLabs API
│   │           │
│   │           ├── parallel_search.py   # 🔍 Parallel search endpoints
│   │           │                         #    - Concurrent profile searches
│   │           │
│   │           ├── preview.py           # 👀 Preview endpoints
│   │           │                         #    - Get all previews
│   │           │                         #    - Get preview by room ID
│   │           │
│   │           ├── scrape.py            # 🕷️  LinkedIn scraping endpoints
│   │           │                         #    - Trigger scraping jobs (Apify)
│   │           │                         #    - Check job status
│   │           │                         #    - Get scraping results
│   │           │
│   │           └── search.py            # 🔎 Profile search endpoints
│   │                                   #    - Search profiles with filters
│   │                                   #    - Save search queries
│   │
│   ├── 📁 models/                       # 📋 DATA MODELS (Pydantic)
│   │   ├── __init__.py
│   │   └── schemas.py                   # 📝 Request/Response schemas
│   │                                    #    - Pydantic models for API validation
│   │                                    #    - Request models (CreateAudienceRoomRequest, etc.)
│   │                                    #    - Response models
│   │
│   ├── 📁 services/                     # 🔧 BUSINESS LOGIC SERVICES
│   │   ├── apify_service.py            # 🔌 Apify API integration
│   │   │                                #    - Actor run management
│   │   │                                #    - Dataset fetching
│   │   │
│   │   ├── classifier_service.py       # 🏷️  Classification logic
│   │   │                                #    - Batch post classification
│   │   │                                #    - Groq LLM integration
│   │   │
│   │   ├── openai_service.py           # 🤖 OpenAI API integration
│   │   │                                #    - Text generation
│   │   │                                #    - Summarization
│   │   │
│   │   ├── profile_service.py          # 👤 Profile processing
│   │   │                                #    - Process posts from Apify
│   │   │                                #    - Update profile records
│   │   │                                #    - S3 upload integration
│   │   │
│   │   └── summary_service.py          # 📄 Summary generation
│   │                                    #    - Profile summary processing
│   │                                    #    - Group summary generation
│   │                                    #    - OpenAI/Groq integration
│   │
│   └── 📁 utils/                        # 🛠️  UTILITY FUNCTIONS
│       ├── helpers.py                   # 🔧 General helper functions
│       │                                #    - Experience calculation
│       │                                #    - SQL query building (PDL)
│       │                                #    - Database availability checks
│       │                                #    - LinkedIn URL normalization
│       │
│       └── s3_utils.py                  # ☁️  AWS S3 utilities
│                                       #    - Upload JSON to S3
│                                       #    - Fetch JSON from S3
│                                       #    - Extract S3 keys from URLs
│
├── 📁 prisma/                           # 🗄️  PRISMA SCHEMA & MIGRATIONS
│   ├── schema.prisma                    # 📋 Main database schema (ScrapeJob, SearchQuery)
│   ├── audience.schema.prisma           # 📋 Audience database schema
│   │                                    #    - AudienceRoom, AudienceProfile
│   │                                    #    - PostClassifier, ChatAssets, etc.
│   │
│   └── migrations/                      # 📜 DATABASE MIGRATIONS
│       ├── migration_lock.toml          # 🔒 Migration lock file (PostgreSQL provider)
│       └── [9 migration folders]/       # 📁 Historical migration files
│           └── migration.sql            #    - Tracks database schema evolution
│                                       #    - Applied to database via Prisma CLI
│
├── 📁 docs/                             # 📚 DOCUMENTATION
│   ├── README.md                        # 📖 Main documentation
│   ├── API_DOCUMENTATION.md             # 📘 API reference
│   ├── DEPLOYMENT.md                    # 🚀 Deployment guide
│   ├── ARCHITECTURE.md                  # 🏗️  Architecture overview
│   ├── ADDING_NEW_TABLES.md             # ➕ Guide for adding new tables
│   └── [20+ other docs]                 # 📄 Various guides and references
│
└── 📁 json/                             # 📦 EXAMPLE/REFERENCE DATA
    ├── EXAMPLE_REQUESTS.json            # 📝 Example API requests
    ├── posts.json                       # 📝 Sample post data
    └── [other JSON files]               # 📄 Reference/test data


----------------------------------------------------------------------------------------------------------------


## 📁 Root Level

```
Audience-workflow/
├── main.py                          # Entry point wrapper
├── requirements.txt                 # Python dependencies
├── package.json                     # Node.js package config
├── vercel.json                      # Vercel deployment configuration
├── vercel_build_prisma.sh           # Build script for Prisma client generation
├── deploy.sh                        # Deployment script
└── .gitignore                       # Git ignore rules
```

### Root Files

| File | Purpose |
|------|---------|
| `main.py` | ⚡ Entry point wrapper - imports app from `app/main.py` (for deployment compatibility) |
| `requirements.txt` | 📦 Python dependencies (FastAPI, psycopg2, OpenAI, Groq, etc.) |
| `package.json` | 📦 Node.js package config (for Prisma generation on Vercel) |
| `vercel.json` | 🚀 Vercel deployment configuration |
| `vercel_build_prisma.sh` | 🔧 Build script for Prisma client generation on Vercel |
| `deploy.sh` | 🚀 Deployment script (Vercel CLI helper) |
| `.gitignore` | 🚫 Git ignore rules (excludes generated Prisma clients) |

---

## 📁 app/ - Main Application Package

```
app/
├── __init__.py                      # Package init - exports database module
├── main.py                          # FastAPI application setup
├── config.py                        # Configuration & client initialization
├── database/                        # Database module (replaces Prisma client)
├── api/                             # API routes
├── models/                          # Data models (Pydantic)
├── services/                        # Business logic services
└── utils/                           # Utility functions
```

### Core Application Files

#### `app/__init__.py`
- **Purpose**: Package initialization file
- **Exports**: Database module for easy imports (`from app import database`)

#### `app/main.py`
- **Purpose**: FastAPI application setup
- **Responsibilities**:
  - Creates FastAPI app instance
  - Configures CORS middleware
  - Sets up lifespan (database connection checks on startup/shutdown)
  - Includes API router
  - Health check endpoint (`/`)

#### `app/config.py`
- **Purpose**: Configuration & client initialization
- **Responsibilities**:
  - Environment variable loading (`python-dotenv`)
  - Third-party client setup:
    - PeopleDataLabs (PDL) client
    - Apify client
    - OpenAI client
    - Groq client
    - AWS S3 client
    - DynamoDB resource (optional)
  - Database availability checks
  - Logging configuration
  - Constants (Actor IDs, etc.)

---

### 📁 app/database/ - Database Module

```
app/database/
├── __init__.py                      # Exports all database functions and models
└── connection.py                    # Database connection & operations
```

#### Purpose
Replaces Prisma client to avoid deployment issues on Vercel. Uses `psycopg2` directly for database operations.

#### `app/database/connection.py`
- **Purpose**: Database connection & operations
- **Key Components**:
  - Connection pooling (separate pools for main & audience databases)
  - Data models (Python classes):
    - `ScrapeJob`
    - `AudienceRoom`
    - `AudienceProfile`
    - `PostClassifier`
  - CRUD operations for all database tables:
    - ScrapeJob operations (main database)
    - AudienceRoom operations (audience database)
    - AudienceProfile operations (audience database)
    - PostClassifier operations (audience database)
    - Preview operations (audience database)
  - Database health checks
  - Connection management (context managers)

#### `app/database/__init__.py`
- **Purpose**: Exports all database functions and models
- **Usage**: `from app import database` then `database.find_scrape_job_by_id(...)`

---

### 📁 app/api/ - API Routes

```
app/api/
└── v1/
    ├── router.py                    # Main API router
    └── endpoints/                   # API endpoint handlers
        ├── audience.py              # Audience room management
        ├── classifier.py            # Post classification
        ├── enrich.py                # Profile enrichment
        ├── parallel_search.py       # Parallel search
        ├── preview.py               # Preview endpoints
        ├── scrape.py                # LinkedIn scraping
        └── search.py                # Profile search
```

#### `app/api/v1/router.py`
- **Purpose**: Main API router that registers all endpoint routers
- **Responsibilities**:
  - Imports all endpoint routers
  - Registers them with the main API router
  - Centralized route registration

#### API Endpoints

##### `app/api/v1/endpoints/audience.py`
- **Purpose**: Audience room management endpoints
- **Endpoints**:
  - `POST /api/v1/audience-rooms` - Create audience room
  - `DELETE /api/v1/audience-rooms/{id}` - Delete audience room
  - `GET /api/v1/audience-rooms/{id}/description` - Get room description
  - `GET /api/v1/audience-rooms/{id}/profiles/{profile_id}/description` - Get profile description
  - `GET /api/v1/audience-rooms/{id}/profiles/{profile_id}/posts` - Get profile posts
  - `POST /api/v1/audience-rooms/{id}/generate-summaries` - Generate profile summaries
  - `POST /api/v1/audience-rooms/{id}/generate-group-summary` - Generate group summary

##### `app/api/v1/endpoints/classifier.py`
- **Purpose**: Post classification endpoints
- **Endpoints**:
  - `POST /api/classifier/run` - Run classifier on all posts in a room
  - `POST /api/classifier/run-profiles` - Run classifier for specific profiles
- **Note**: Uses Groq LLM for classification

##### `app/api/v1/endpoints/enrich.py`
- **Purpose**: Profile enrichment endpoints
- **Functionality**: Enrich profiles using PeopleDataLabs API

##### `app/api/v1/endpoints/parallel_search.py`
- **Purpose**: Parallel search endpoints
- **Functionality**: Concurrent profile searches

##### `app/api/v1/endpoints/preview.py`
- **Purpose**: Preview endpoints
- **Endpoints**:
  - `GET /api/v1/previews` - Get all previews (optionally filtered by user_id)
  - `GET /api/v1/previews/{room_id}` - Get preview by room ID

##### `app/api/v1/endpoints/scrape.py`
- **Purpose**: LinkedIn scraping endpoints
- **Endpoints**:
  - `POST /api/v1/scrape` - Trigger scraping job (Apify)
  - `GET /api/v1/scrape/status/{job_id}` - Check job status
  - `GET /api/v1/scrape/result/{job_id}` - Get scraping results
- **Functionality**: Manages asynchronous scraping jobs via Apify

##### `app/api/v1/endpoints/search.py`
- **Purpose**: Profile search endpoints
- **Functionality**:
  - Search profiles with filters
  - Save search queries to database

---

### 📁 app/models/ - Data Models (Pydantic)

```
app/models/
├── __init__.py
└── schemas.py                       # Request/Response schemas
```

#### `app/models/schemas.py`
- **Purpose**: Pydantic models for API validation
- **Contains**:
  - Request models (e.g., `CreateAudienceRoomRequest`, `ScrapeRequest`)
  - Response models
  - Data validation schemas
- **Usage**: Validates incoming request data and defines response structures

---

### 📁 app/services/ - Business Logic Services

```
app/services/
├── apify_service.py                 # Apify API integration
├── classifier_service.py            # Classification logic
├── openai_service.py                # OpenAI API integration
├── profile_service.py               # Profile processing
└── summary_service.py               # Summary generation
```

#### Service Files

##### `app/services/apify_service.py`
- **Purpose**: Apify API integration
- **Functionality**:
  - Actor run management
  - Dataset fetching
  - Job status tracking

##### `app/services/classifier_service.py`
- **Purpose**: Classification logic
- **Functionality**:
  - Batch post classification
  - Groq LLM integration
  - Label assignment

##### `app/services/openai_service.py`
- **Purpose**: OpenAI API integration
- **Functionality**:
  - Text generation
  - Summarization
  - Profile analysis

##### `app/services/profile_service.py`
- **Purpose**: Profile processing
- **Functionality**:
  - Process posts from Apify datasets
  - Update profile records in database
  - S3 upload integration
  - Profile data normalization

##### `app/services/summary_service.py`
- **Purpose**: Summary generation
- **Functionality**:
  - Profile summary processing
  - Group summary generation
  - OpenAI/Groq integration for summaries
  - S3 storage management

---

### 📁 app/utils/ - Utility Functions

```
app/utils/
├── __init__.py
├── helpers.py                       # General helper functions
└── s3_utils.py                      # AWS S3 utilities
```

#### `app/utils/helpers.py`
- **Purpose**: General helper functions
- **Functions**:
  - `calculate_experience_years()` - Calculates work experience from job history
  - `build_pdl_sql()` - Constructs PDL SQL queries from filters
  - `ensure_db_available()` - Checks database availability
  - `normalize_linkedin_url()` - Normalizes LinkedIn URLs for matching

#### `app/utils/s3_utils.py`
- **Purpose**: AWS S3 utilities
- **Functions**:
  - `upload_json_to_s3()` - Upload JSON data to S3
  - `fetch_json_from_s3()` - Fetch JSON data from S3
  - `extract_s3_key_from_url()` - Extract S3 key from URL
- **Usage**: Manages all S3 operations for storing profile data, posts, summaries, etc.

---

## 📁 prisma/ - Database Schema & Migrations

```
prisma/
├── schema.prisma                    # Main database schema
├── audience.schema.prisma           # Audience database schema
└── migrations/                      # Database migrations
    ├── migration_lock.toml          # Migration lock file
    └── [9 migration folders]/       # Historical migration files
        └── migration.sql
```

### Prisma Files

#### `prisma/schema.prisma`
- **Purpose**: Main database schema definition
- **Models**:
  - `ScrapeJob` - Scraping job tracking
  - `SearchQuery` - Saved search queries
- **Database**: Main PostgreSQL database (`DATABASE_URL`)

#### `prisma/audience.schema.prisma`
- **Purpose**: Audience database schema definition
- **Models**:
  - `AudienceRoom` - Audience room containers
  - `AudienceProfile` - Profile data within rooms
  - `PostClassifier` - Classification configurations
  - `ChatAssets` - Chat-related assets
  - `CustomClone` - Custom clone data
  - `PreMadePrompt` - Pre-made prompts
  - `ScrapeJob` - Scraping jobs (audience DB)
  - `SearchQuery` - Search queries (audience DB)
  - `StoryActions`, `StoryComment` - Story-related models
  - `VapiCallConfig`, `VapiToolResult` - VAPI-related models
- **Database**: Audience PostgreSQL database (`AUDIENCE_DATABASE_URL`)

#### `prisma/migrations/`
- **Purpose**: Database migration history
- **Contains**: 9 migration folders tracking schema evolution
- **Usage**: Applied to database via Prisma CLI (`prisma migrate deploy`)

---

## 📁 docs/ - Documentation

```
docs/
├── README.md                        # Main documentation
├── API_DOCUMENTATION.md             # API reference
├── DEPLOYMENT.md                    # Deployment guide
├── ARCHITECTURE.md                  # Architecture overview
├── ADDING_NEW_TABLES.md             # Guide for adding new tables
└── [20+ other documentation files]  # Various guides and references
```

### Key Documentation Files

- **API_DOCUMENTATION.md**: Complete API endpoint documentation
- **DEPLOYMENT.md**: Step-by-step deployment guide
- **ARCHITECTURE.md**: System architecture explanation
- **ADDING_NEW_TABLES.md**: Guide for adding new database tables
- **CLASSIFIER_API_DOCUMENTATION.md**: Classifier-specific documentation
- **TESTING.md**: Testing guidelines

---

## 📁 json/ - Example/Reference Data

```
json/
├── EXAMPLE_REQUESTS.json            # Example API requests
├── posts.json                       # Sample post data
└── [other JSON files]               # Reference/test data
```

---

## 🏗️ Architecture Overview

### Modular Structure
- **`app/`** contains all application code
- **`app/database/`** handles all database operations (replaces Prisma client)
- **`app/api/`** contains all API endpoints organized by domain
- **`app/services/`** contains business logic separate from endpoints
- **`app/utils/`** contains reusable utility functions

### Database Strategy
- Uses `psycopg2` directly (not Prisma client) to avoid Vercel deployment issues
- Prisma schemas (`prisma/*.schema.prisma`) define the database structure
- Migrations in `prisma/migrations/` track schema history
- Generated Prisma clients are ignored (not needed, can be regenerated)

### API Organization
- All endpoints under `/api/v1/`
- Endpoints grouped by domain (audience, scrape, search, etc.)
- Each endpoint file handles related routes
- Router in `app/api/v1/router.py` registers all endpoints

### Configuration
- Environment variables loaded via `python-dotenv`
- Clients initialized in `app/config.py`
- Database connections managed in `app/database/connection.py`

### Deployment
- Vercel-ready configuration (`vercel.json`, `vercel_build_prisma.sh`)
- Health checks and CORS configured
- Database connection pooling for scalability

---

## 🔑 Key Design Decisions

1. **Database Layer**: Uses `psycopg2` directly instead of Prisma client for better Vercel compatibility
2. **Modular Structure**: Clear separation of concerns (API, services, database, utils)
3. **Dual Databases**: Separate main and audience databases with different schemas
4. **Service Layer**: Business logic separated from API endpoints for reusability
5. **S3 Storage**: Profile data, posts, and summaries stored in S3 for scalability
6. **Async Operations**: Scraping jobs run asynchronously with status tracking

---

## 📝 Notes

- Generated Prisma clients (`prisma_client/`, `audience_prisma_client/`) are in `.gitignore` and can be regenerated
- Migrations are now consolidated in `prisma/migrations/` (standard Prisma location)
- All database code is in `app/database/` module for better organization
- Environment variables required: `DATABASE_URL`, `AUDIENCE_DATABASE_URL`, API keys, etc.

---

**Last Updated**: December 2024  
**Version**: 1.0.0

