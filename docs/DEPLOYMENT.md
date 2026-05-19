# 🚀 Deployment Guide - Vercel

## Prerequisites

1. ✅ Vercel CLI installed: `npm i -g vercel`
2. ✅ Vercel account connected
3. ✅ Neon database set up
4. ✅ All code committed to git (recommended)

---

## Step 1: Set Environment Variables in Vercel

### Option A: Via Vercel Dashboard (Recommended)

1. Go to your Vercel project: https://vercel.com/dashboard
2. Click on your project
3. Go to **Settings** → **Environment Variables**
4. Add these variables:

```
DATABASE_URL=postgresql://neondb_owner:npg_3adSH8IXbJMo@ep-holy-cell-a455b6ph-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require

PDL_API_KEY=your_actual_pdl_key
APIFY_API_TOKEN=your_actual_apify_token
OPENAI_API_KEY=your_actual_openai_key

AWS_REGION=us-west-2
AUDIENCE_API_BASE_URL=https://vectorial-api-code.vercel.app
```

**Important**: 
- Use the **pooled** connection URL (with `-pooler` in hostname) for serverless
- Apply to: **Production**, **Preview**, and **Development**

### Option B: Via Vercel CLI

```bash
# Set environment variables
vercel env add DATABASE_URL production
# Paste: postgresql://neondb_owner:npg_3adSH8IXbJMo@ep-holy-cell-a455b6ph-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require

vercel env add PDL_API_KEY production
vercel env add APIFY_API_TOKEN production
vercel env add OPENAI_API_KEY production
vercel env add AWS_REGION production
```

---

## Step 2: Update .gitignore

Make sure sensitive files are ignored:

```bash
# Check .gitignore
cat .gitignore
```

Should include:
```
.env
__pycache__/
*.pyc
.vercel/
```

---

## Step 3: Deploy to Vercel

### First Time Deployment

```bash
# Login to Vercel (if not already)
vercel login

# Deploy
vercel

# Follow prompts:
# - Set up and deploy? Yes
# - Which scope? (your account)
# - Link to existing project? Yes (if updating) or No (if new)
# - Project name? (your project name)
```

### Update Existing Deployment

```bash
# Deploy to production
vercel --prod

# Or deploy to preview
vercel
```

---

## Step 4: Run Prisma Migrations on Production

After deployment, you need to run migrations on your production database:

### Option A: Using Vercel CLI (Recommended)

```bash
# Set DATABASE_URL for migrations (use unpooled connection)
export DATABASE_URL="postgresql://neondb_owner:npg_3adSH8IXbJMo@ep-holy-cell-a455b6ph.us-east-1.aws.neon.tech/neondb?sslmode=require"

# Run migrations
prisma migrate deploy
```

### Option B: Using Neon Dashboard

1. Go to Neon dashboard
2. Open SQL Editor
3. Run the migration SQL from `prisma/migrations/20251129160713_init/migration.sql`

### Option C: Using Prisma Studio (for testing)

```bash
# Connect to production DB (be careful!)
export DATABASE_URL="your_production_database_url"
prisma studio
```

---

## Step 5: Verify Deployment

### 1. Check Health Endpoint

```bash
curl https://your-project.vercel.app/
```

Expected:
```json
{"status": "ok", "message": "Backend is running"}
```

### 2. Test Scraping Endpoint

```bash
curl -X POST https://your-project.vercel.app/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_urls": ["https://linkedin.com/in/example"],
    "max_posts": 10,
    "cookies": [...],
    "user_agent": "..."
  }'
```

Should return `job_id` immediately.

### 3. Check Logs

```bash
# View deployment logs
vercel logs

# Or in dashboard: Project → Deployments → Click deployment → View Function Logs
```

---

## Step 6: Post-Deployment Checklist

- [ ] Environment variables set in Vercel
- [ ] Database migrations run on production
- [ ] Health endpoint working
- [ ] Can create scraping jobs
- [ ] Can check job status
- [ ] Database connection working (check logs)

---

## Troubleshooting

### Issue: "Prisma client not generated"

**Solution**: Prisma client is generated during build. Make sure:
- `prisma` is in `requirements.txt`
- `prisma/schema.prisma` is in repository
- Build logs show Prisma generation

### Issue: "Database connection failed"

**Solution**:
1. Check `DATABASE_URL` in Vercel environment variables
2. Use **pooled** connection (with `-pooler`)
3. Check Neon dashboard for connection limits
4. Verify SSL mode: `?sslmode=require`

### Issue: "Table does not exist"

**Solution**: Run migrations:
```bash
export DATABASE_URL="your_production_url"
prisma migrate deploy
```

### Issue: "Module not found: prisma"

**Solution**: 
- Check `requirements.txt` has `prisma==0.11.0`
- Redeploy: `vercel --prod`

### Issue: "Function timeout"

**Solution**: 
- ✅ This should be fixed with async architecture
- If still happening, check you're using `.start()` not `.call()`
- Check Vercel plan limits (Pro has 60s, Enterprise has 300s)

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ Yes | Neon PostgreSQL connection (pooled) |
| `PDL_API_KEY` | ✅ Yes | People Data Labs API key |
| `APIFY_API_TOKEN` | ✅ Yes | Apify API token |
| `OPENAI_API_KEY` | ✅ Yes | OpenAI API key |
| `AUDIENCE_API_BASE_URL` | ✅ Yes (async pipelines) | Public FastAPI URL for chunked job callbacks (e.g. `https://vectorial-api-code.vercel.app`) |
| `AWS_REGION` | ⚠️ Optional | AWS region (if using DynamoDB) |

---

## Deployment Commands Cheat Sheet

```bash
# Deploy to production
vercel --prod

# Deploy to preview
vercel

# View logs
vercel logs

# List deployments
vercel ls

# Open project in browser
vercel open

# Run migrations locally (test)
prisma migrate deploy

# Check Prisma status
prisma migrate status
```

---

## Important Notes

1. **Database URL**: Always use **pooled** connection for serverless (with `-pooler`)
2. **Migrations**: Run `prisma migrate deploy` after first deployment
3. **Prisma Client**: Generated automatically during Vercel build
4. **Environment Variables**: Must be set in Vercel dashboard, not just `.env`
5. **Cold Starts**: First request may be slow (Prisma connection), subsequent requests are fast

---

## Next Steps After Deployment

1. ✅ Test all endpoints
2. ✅ Monitor logs for errors
3. ✅ Check database for job records
4. ✅ Update frontend to use new async endpoints
5. ✅ Set up monitoring/alerts (optional)

---

## Rollback (If Needed)

If something goes wrong:

```bash
# List deployments
vercel ls

# Promote previous deployment
vercel promote <deployment-url>

# Or redeploy previous commit
git checkout <previous-commit>
vercel --prod
```

