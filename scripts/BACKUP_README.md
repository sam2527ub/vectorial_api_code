# Database Backup Scripts

These scripts create **READ-ONLY** backups of your audience database. They do **NOT** modify or delete anything in your database - they only read data.

## 📦 What Gets Backed Up

All tables from the `audience` database, including:
- `AudienceRoom`
- `AudienceProfile`
- `PostClassifier`
- `ChatAssets`
- `CustomClone`
- `PreMadePrompt`
- `ScrapeJob`
- `SearchQuery`
- `StoryActions`
- `StoryComment`
- `VapiCallConfig`
- `VapiToolResult`
- `previews`
- Any other tables in the database

## 🚀 Quick Start

### Option 1: Run Both Backups (Recommended)

Run both CSV and pg_dump backups in one command:

```bash
./scripts/run_backup.sh
```

This will create:
- **CSV backup**: `~/Desktop/backup/csv_YYYYMMDD_HHMMSS/` (one CSV file per table)
- **pg_dump backup**: `~/Desktop/backup/pg_dump_YYYYMMDD_HHMMSS/` (SQL dump file)

### Option 2: Run Backups Separately

#### CSV Backup Only

```bash
python app/utils/backup_database.py
```

Output: `~/Desktop/backup/csv_YYYYMMDD_HHMMSS/`

#### pg_dump Backup Only

```bash
./scripts/backup_audience_db.sh
```

Output: `~/Desktop/backup/pg_dump_YYYYMMDD_HHMMSS/`

## 📋 Prerequisites

1. **Environment Variable**: Make sure `AUDIENCE_DATABASE_URL` is set:
   ```bash
   export AUDIENCE_DATABASE_URL='postgresql://user:password@host:port/database'
   ```

2. **Python Dependencies**: Ensure `psycopg2-binary` is installed (already in `requirements.txt`)

3. **PostgreSQL Tools**: For pg_dump, you need `pg_dump` and `gzip` installed (usually comes with PostgreSQL)

## 📁 Backup Formats

### CSV Backup
- **Format**: One CSV file per table
- **Location**: `~/Desktop/backup/csv_TIMESTAMP/`
- **Files**: 
  - `TableName.csv` (one per table)
  - `_backup_metadata.txt` (backup info)
- **Best for**: 
  - Easy inspection in Excel/spreadsheet apps
  - Selective data extraction
  - Human-readable format

### pg_dump Backup
- **Format**: SQL dump file
- **Location**: `~/Desktop/backup/pg_dump_TIMESTAMP/`
- **Files**:
  - `audience_db_backup.sql` (full SQL dump)
  - `audience_db_backup.sql.gz` (compressed version)
  - `_backup_metadata.txt` (backup info)
- **Best for**:
  - Complete database restoration
  - Includes schema, indexes, constraints
  - Industry standard format
  - Smaller file size (compressed)

## ✅ Safety Guarantees

- **100% READ-ONLY**: These scripts only use `SELECT` queries (CSV) or `pg_dump` (read-only)
- **No Deletes**: Nothing is deleted from your database
- **No Updates**: No data is modified
- **No Schema Changes**: Database structure remains unchanged

## 🔄 Restoring from Backup

### Restore from CSV Backup

⚠️ **Note**: CSV restore scripts are not included by default as they modify the database. 
If you need to restore from CSV, you'll need to manually import or use a restore script.

### Restore from pg_dump Backup

```bash
# Restore from SQL file
psql -h HOST -p PORT -U USER -d DATABASE -f ~/Desktop/backup/pg_dump_TIMESTAMP/audience_db_backup.sql

# Or from compressed file
gunzip -c ~/Desktop/backup/pg_dump_TIMESTAMP/audience_db_backup.sql.gz | psql -h HOST -p PORT -U USER -d DATABASE
```

## 📝 Notes

- Backups are timestamped, so you can keep multiple backups
- All backups are stored in `~/Desktop/backup/`
- Each backup includes a `_backup_metadata.txt` file with details
- Large databases may take a few minutes to backup
- pg_dump creates both regular and compressed (.gz) versions





