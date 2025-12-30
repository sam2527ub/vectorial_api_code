# Profile Engine API - Async Scraper Backend

FastAPI backend for PDL Enrichment, Search, and LinkedIn Scraping with async job processing.

## Architecture

This backend uses an **async job queue pattern** to handle long-running scraping tasks (5-7 minutes) that would otherwise timeout on Vercel's serverless functions.

### How It Works

1. **POST `/api/v1/scrape`** - Creates a job and starts Apify scraping asynchronously
   - Returns `job_id` immediately (< 1 second)
   - Scraping runs in background on Apify

2. **GET `/api/v1/scrape/status/{job_id}`** - Poll this endpoint to check job status
   - Returns `PENDING`, `PROCESSING`, `COMPLETED`, or `FAILED`
   - When `COMPLETED`, returns the scraped data
   - Frontend should poll every 3-5 seconds

3. **Database** - Prisma + PostgreSQL stores job status and results
   - Jobs persist across server restarts
   - Results are cached for quick retrieval

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Prisma

```bash
# Generate Prisma client
prisma generate

# Run migrations (after setting up database)
prisma migrate dev
```

### 3. Environment Variables

Create a `.env` file with:

```env
# Database (Required)
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
# Audience DB (Optional - used for saved audiences; falls back to PRISMA_DATABASE_URL)
AUDIENCE_DATABASE_URL=postgresql://user:password@localhost:5432/audience_db

# API Keys (Required)
PDL_API_KEY=your_pdl_api_key_here
APIFY_API_TOKEN=your_apify_api_token_here
OPENAI_API_KEY=your_openai_api_key_here

# AWS (Optional - for DynamoDB if still using)
AWS_REGION=us-west-2
```

### 4. Database Options

For Vercel deployment, use one of these PostgreSQL services:

- **Vercel Postgres** (recommended) - Built-in, free tier available
- **Neon** - Serverless Postgres, free tier available
- **Supabase** - Open source Firebase alternative, free tier available

## Deployment to Vercel

1. **Set Environment Variables** in Vercel dashboard:
   - `DATABASE_URL`
   - `PDL_API_KEY`
   - `APIFY_API_TOKEN`
   - `OPENAI_API_KEY`
   - `AWS_REGION` (if using DynamoDB)

2. **Deploy**:
   ```bash
   vercel
   ```

3. **Run Prisma Migrations**:
   After first deployment, run migrations on your production database:
   ```bash
   prisma migrate deploy
   ```

## API Endpoints

### Health Check
- `GET /` - Returns API status

### Job Title Enrichment
- `POST /api/v1/enrich` - Enrich a job title using PDL

### Filter Extraction
- `POST /api/v1/extract-filters` - Extract search filters from natural language using OpenAI

### Profile Search
- `POST /api/v1/search` - Search profiles using PDL with filters

### Async Scraping
- `POST /api/v1/scrape` - Start a scraping job (returns `job_id`)
- `GET /api/v1/scrape/status/{job_id}` - Check job status and get results

## Frontend Integration

```javascript
// 1. Start scraping job
const response = await fetch('/api/v1/scrape', {
  method: 'POST',
  body: JSON.stringify({
    linkedin_urls: ['https://linkedin.com/in/profile1'],
    max_posts: 25,
    cookies: [...],
    user_agent: '...'
  })
});
const { job_id } = await response.json();

// 2. Poll for status
const pollStatus = async () => {
  const statusResponse = await fetch(`/api/v1/scrape/status/${job_id}`);
  const status = await statusResponse.json();
  
  if (status.status === 'COMPLETED') {
    console.log('Results:', status.data);
    return status.data;
  } else if (status.status === 'FAILED') {
    console.error('Error:', status.error);
    return null;
  } else {
    // Still processing, poll again in 3 seconds
    setTimeout(pollStatus, 3000);
  }
};

pollStatus();
```

## Benefits

- ✅ No timeout issues (instant API response)
- ✅ Better UX (progress tracking)
- ✅ Scalable (handles multiple concurrent scrapes)
- ✅ Persistent (survives server restarts)
- ✅ Cached results (quick retrieval)

