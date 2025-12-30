# Adding New Tables to Existing Prisma + PostgreSQL Database

## Overview
This guide shows how to safely add new tables to your existing Prisma + PostgreSQL database without breaking existing data.

## ✅ What We Just Did

1. **Added a new model** (`SearchQuery`) to `schema.prisma`
2. **Created a migration** that only adds the new table (doesn't touch existing `ScrapeJob` table)

## Current Status

- ✅ Schema updated: `prisma/schema.prisma` now includes `SearchQuery` model
- ✅ Migration created: `prisma/migrations/20251201004939_add_search_query_table/migration.sql`
- ⏳ **Next step**: Generate Prisma client and apply migration

## Next Steps

### 1. Generate Prisma Client

After adding a new model, you need to regenerate the Prisma client:

```bash
prisma generate
```

This updates the Python Prisma client to include the new `SearchQuery` model.

### 2. Apply the Migration

To apply the migration to your database:

**For Development:**
```bash
prisma migrate dev
```

**For Production (Vercel/Deployed):**
```bash
prisma migrate deploy
```

This will:
- ✅ Create the new `SearchQuery` table
- ✅ Add indexes
- ✅ **NOT touch** existing `ScrapeJob` table or data

## How to Add Your Own Table

### Step 1: Add Model to `prisma/schema.prisma`

```prisma
model YourNewTable {
  id        String   @id @default(uuid())
  name      String
  data      Json?
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([createdAt])
}
```

### Step 2: Create Migration

**Option A: Automatic (Recommended if DATABASE_URL is set)**
```bash
prisma migrate dev --name add_your_new_table
```

**Option B: Manual (If DATABASE_URL not available)**
1. Create directory: `migrations/TIMESTAMP_add_your_new_table/`
2. Create `migration.sql` with CREATE TABLE statement
3. Follow Prisma migration naming conventions

### Step 3: Generate Client & Apply

```bash
prisma generate
prisma migrate deploy  # or migrate dev for development
```

## Safety Guarantees

✅ **Existing tables are NOT modified** - Only new tables are created
✅ **Existing data is preserved** - No DROP or ALTER statements on existing tables
✅ **Migrations are additive** - Each migration only adds new structures

## Example: Using the New SearchQuery Table

```python
from prisma import Prisma

prisma = Prisma()
await prisma.connect()

# Create a new search query record
search_query = await prisma.searchquery.create(
    data={
        "filters": {
            "titles": ["Software Engineer"],
            "locations": ["San Francisco"]
        },
        "sqlQuery": "SELECT * FROM person WHERE job_title IN ('Software Engineer')",
        "resultCount": 42
    }
)

# Query search history
recent_searches = await prisma.searchquery.find_many(
    order={"createdAt": "desc"},
    take=10
)

await prisma.disconnect()
```

## Migration File Structure

```
migrations/
├── 20251129160713_init/          # Original migration (ScrapeJob)
│   └── migration.sql
├── 20251201004939_add_search_query_table/  # New migration
│   └── migration.sql
└── migration_lock.toml
```

## Troubleshooting

### Issue: "Prisma client not generated"
**Solution**: Run `prisma generate` after schema changes

### Issue: "Migration already applied"
**Solution**: Check migration status with `prisma migrate status`

### Issue: "DATABASE_URL not found"
**Solution**: Set `DATABASE_URL` in `.env` file:
```
DATABASE_URL="postgresql://user:password@host:5432/database"
```

### Issue: Want to modify existing table instead?
**Solution**: Use `prisma migrate dev` with schema changes - Prisma will generate ALTER TABLE statements safely

## Best Practices

1. ✅ Always create migrations for schema changes
2. ✅ Test migrations on development database first
3. ✅ Use descriptive migration names
4. ✅ Review generated SQL before applying in production
5. ✅ Keep migrations small and focused (one table per migration if possible)

## Current Database Schema

- **ScrapeJob**: Tracks LinkedIn scraping jobs
- **SearchQuery**: Tracks PDL search queries (NEW)
