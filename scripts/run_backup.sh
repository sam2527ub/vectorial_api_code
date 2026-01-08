#!/bin/bash
# Master backup script - runs both CSV and pg_dump backups
# READ-ONLY OPERATIONS - Does not modify the database

echo "═══════════════════════════════════════════════════════════"
echo "🔄 Audience Database Backup (READ-ONLY)"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "This script will create TWO backups:"
echo "  1. CSV backup (one file per table)"
echo "  2. SQL dump backup (SQL file with INSERT statements)"
echo ""
echo "✅ Both backups are READ-ONLY - no database modifications"
echo ""

# Check if we're in the project directory
if [[ ! -f "requirements.txt" ]]; then
    echo "❌ Error: Please run this script from the project root directory"
    exit 1
fi

# Load .env file if it exists
if [[ -f ".env" ]]; then
    echo "📝 Loading environment variables from .env file..."
    export $(grep -v '^#' .env | xargs)
fi

# Check for database URL
if [[ -z "$AUDIENCE_DATABASE_URL" ]]; then
    echo "❌ Error: AUDIENCE_DATABASE_URL environment variable not set"
    echo ""
    echo "Please either:"
    echo "  1. Set it using: export AUDIENCE_DATABASE_URL='postgresql://user:pass@host:port/dbname'"
    echo "  2. Add it to a .env file in the project root"
    exit 1
fi

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "📂 Project directory: $PROJECT_DIR"
echo ""

# Run CSV backup
echo "───────────────────────────────────────────────────────────"
echo "1️⃣  Starting CSV Backup..."
echo "───────────────────────────────────────────────────────────"
cd "$PROJECT_DIR"

# Check if virtual environment is activated, if not try to activate
if [[ -z "$VIRTUAL_ENV" ]]; then
    if [[ -d "venv" ]]; then
        source venv/bin/activate
    elif [[ -d ".venv" ]]; then
        source .venv/bin/activate
    fi
fi

python app/utils/backup_database.py
CSV_STATUS=$?

echo ""
echo "───────────────────────────────────────────────────────────"
echo "2️⃣  Starting SQL Dump Backup..."
echo "───────────────────────────────────────────────────────────"

# Run SQL dump backup (Python-based, doesn't require pg_dump)
python app/utils/backup_sql_dump.py
SQLDUMP_STATUS=$?

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "📊 Backup Summary"
echo "═══════════════════════════════════════════════════════════"

if [ $CSV_STATUS -eq 0 ] && [ $SQLDUMP_STATUS -eq 0 ]; then
    echo ""
    echo "✅ Both backups completed successfully!"
    echo ""
    echo "📁 All backups are saved to: ~/Desktop/backup/"
    echo ""
    echo "   • CSV backup: ~/Desktop/backup/csv_YYYYMMDD_HHMMSS/"
    echo "   • SQL dump backup: ~/Desktop/backup/sql_YYYYMMDD_HHMMSS/"
    echo ""
    ls -lh "$HOME/Desktop/backup/" 2>/dev/null | tail -n +2 || echo "   (Check Desktop/backup folder)"
    echo ""
else
    echo ""
    if [ $CSV_STATUS -ne 0 ]; then
        echo "❌ CSV backup failed!"
    fi
    if [ $SQLDUMP_STATUS -ne 0 ]; then
        echo "❌ SQL dump backup failed!"
    fi
    exit 1
fi

