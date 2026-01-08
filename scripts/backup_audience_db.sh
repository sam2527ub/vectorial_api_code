#!/bin/bash
# Backup audience database using pg_dump
# READ-ONLY OPERATION - Does not modify the database

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

# Load .env file if it exists (using Python to properly parse it)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    # Use Python to load and export .env variables
    export $(python3 -c "
import os
from dotenv import load_dotenv
load_dotenv('$PROJECT_DIR/.env')
for key, value in os.environ.items():
    if 'DATABASE' in key or 'POSTGRES' in key:
        # Escape special characters in value
        value_escaped = value.replace('\'', '\'\\\'\'')
        print(f\"export {key}='{value_escaped}'\")
" 2>/dev/null)
fi

# Get database URL from environment
DATABASE_URL="${AUDIENCE_DATABASE_URL}"

if [[ -z "$DATABASE_URL" ]]; then
    echo "❌ Error: AUDIENCE_DATABASE_URL environment variable not set"
    echo ""
    echo "Please either:"
    echo "  1. Set it using: export AUDIENCE_DATABASE_URL='postgresql://user:pass@host:port/dbname'"
    echo "  2. Add it to a .env file in the project root"
    exit 1
fi

echo ""
echo "🔄 Starting pg_dump backup (READ-ONLY operation)..."
echo ""

# Extract components from database URL
# Format: postgresql://user:password@host:port/database
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')

# Handle case where port might not be in URL (default 5432)
if [[ -z "$DB_PORT" ]]; then
    DB_PORT=5432
fi

# Create backup directory with timestamp on Desktop
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="$HOME/Desktop/backup/pg_dump_${TIMESTAMP}"
mkdir -p "$BACKUP_DIR"

# Backup filename
BACKUP_FILE="${BACKUP_DIR}/audience_db_backup.sql"

echo "📊 Database Info:"
echo "   Host: $DB_HOST"
echo "   Port: $DB_PORT"
echo "   Database: $DB_NAME"
echo "   User: $DB_USER"
echo ""
echo "📁 Backup location: $BACKUP_DIR"
echo ""

# Set password for pg_dump
export PGPASSWORD="$DB_PASS"

# Run pg_dump (READ-ONLY - no modifications to database)
echo "⏳ Creating backup..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --clean \
    --if-exists \
    --format=plain \
    --verbose \
    --file="$BACKUP_FILE" 2>&1 | grep -E "(dumping|ERROR|WARNING)" || true

BACKUP_STATUS=$?

# Unset password
unset PGPASSWORD

if [ $BACKUP_STATUS -eq 0 ]; then
    # Get file size
    FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    
    echo ""
    echo "✅ Backup completed successfully!"
    echo "📁 Backup file: $BACKUP_FILE"
    echo "📦 File size: $FILE_SIZE"
    
    # Create compressed version
    echo ""
    echo "⏳ Creating compressed backup..."
    gzip -k "$BACKUP_FILE"
    COMPRESSED_SIZE=$(du -h "${BACKUP_FILE}.gz" | cut -f1)
    echo "✅ Compressed backup: ${BACKUP_FILE}.gz ($COMPRESSED_SIZE)"
    
    # Save metadata
    METADATA_FILE="${BACKUP_DIR}/_backup_metadata.txt"
    cat > "$METADATA_FILE" << EOF
pg_dump Backup created: $(date -Iseconds)
Database Host: $DB_HOST:$DB_PORT
Database Name: $DB_NAME
Backup File: $(basename "$BACKUP_FILE")
Compressed File: $(basename "${BACKUP_FILE}.gz")
Backup Size: $FILE_SIZE
Compressed Size: $COMPRESSED_SIZE

To restore this backup:
  psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f "$BACKUP_FILE"

Or for compressed backup:
  gunzip -c "${BACKUP_FILE}.gz" | psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME
EOF
    
    echo ""
    echo "📄 Metadata saved to: $METADATA_FILE"
    echo ""
    echo "✅ All backups completed!"
else
    echo ""
    echo "❌ Backup failed! (Exit code: $BACKUP_STATUS)"
    exit 1
fi

