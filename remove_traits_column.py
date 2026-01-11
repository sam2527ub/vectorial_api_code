#!/usr/bin/env python3
"""
Script to remove the traits column from the previews table in two databases.

WARNING: This ONLY modifies the previews table. No other tables are touched.
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor

# Database URLs
DB_1 = "postgres://167d1648f8eaaa7fe51e5af9d382bd10e2743c2a60e5bb2c73b3aa02a5f85970:sk_2kLIhLxqqyVL3KKShSCHg@db.prisma.io:5432/?sslmode=require"
DB_2 = "postgres://15052ad88d989e7db161dd6248d98ecb54887231a77183cbf9158377e12012b6:sk_RWpydOb2grXyLCJ5VxRZc@db.prisma.io:5432/?sslmode=require"


def log(message: str, level: str = "INFO"):
    """Print a log message."""
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
    }.get(level, "  ")
    print(f"{prefix} {message}")


def check_column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name = %s AND column_name = %s
            )
        """, (table_name, column_name))
        result = cur.fetchone()
        return result['exists'] if result else False


def drop_traits_column(conn, db_name: str):
    """Drop the traits column from previews table if it exists."""
    try:
        # Check if column exists
        column_exists = check_column_exists(conn, 'previews', 'traits')
        
        if not column_exists:
            log(f"Database {db_name}: traits column does not exist in previews table", "INFO")
            return True
        
        log(f"Database {db_name}: traits column exists, dropping it...", "INFO")
        
        # Drop the column
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "previews" DROP COLUMN IF EXISTS traits')
        
        conn.commit()
        
        # Verify it's gone
        column_exists_after = check_column_exists(conn, 'previews', 'traits')
        
        if column_exists_after:
            log(f"Database {db_name}: ERROR - traits column still exists after drop!", "ERROR")
            return False
        else:
            log(f"Database {db_name}: Successfully removed traits column from previews table", "SUCCESS")
            return True
            
    except Exception as e:
        log(f"Database {db_name}: Error removing traits column: {e}", "ERROR")
        conn.rollback()
        return False


def main():
    """Main function to remove traits column from both databases."""
    print("\n" + "=" * 70)
    print("🗑️  REMOVE TRAITS COLUMN FROM PREVIEWS TABLE")
    print("=" * 70)
    print("  This script will ONLY modify the 'previews' table")
    print("  No other tables will be touched")
    print("=" * 70 + "\n")
    
    databases = [
        ("Database 1 (GAMMA)", DB_1),
        ("Database 2", DB_2)
    ]
    
    results = []
    
    for db_name, db_url in databases:
        log(f"Connecting to {db_name}...", "INFO")
        
        try:
            conn = psycopg2.connect(db_url)
            log(f"Connected to {db_name}", "SUCCESS")
            
            success = drop_traits_column(conn, db_name)
            results.append((db_name, success))
            
            conn.close()
            log(f"Disconnected from {db_name}", "INFO")
            
        except Exception as e:
            log(f"Failed to connect to {db_name}: {e}", "ERROR")
            results.append((db_name, False))
    
    # Print summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    for db_name, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"  {db_name}: {status}")
    print("=" * 70 + "\n")
    
    # Exit with error if any failed
    if not all(success for _, success in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

