#!/usr/bin/env python3
"""
Script to migrate PostClassifier entries from gamma database to app database.
Usage:
    GAMMA_DATABASE_URL="postgresql://..." APP_DATABASE_URL="postgresql://..." python migrate_post_classifiers.py
    OR
    python migrate_post_classifiers.py --gamma-url "postgresql://..." --app-url "postgresql://..."
    OR
    python migrate_post_classifiers.py  (will prompt for URLs)
"""

import os
import asyncio
import json
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add paths for Prisma client imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    import audience_prisma_client
    AudiencePrisma = audience_prisma_client.Prisma
except ImportError as e:
    print(f"Error importing audience_prisma_client: {e}")
    print("Make sure the Prisma client is generated. Run: prisma generate --schema=audience.schema.prisma")
    sys.exit(1)


def get_connection_strings():
    """Get connection strings from args, env, or prompt."""
    parser = argparse.ArgumentParser(description='Migrate PostClassifier entries between databases')
    parser.add_argument('--gamma-url', help='Gamma database connection URL')
    parser.add_argument('--app-url', help='App database connection URL')
    args = parser.parse_args()
    
    gamma_url = args.gamma_url or os.getenv("GAMMA_DATABASE_URL")
    app_url = args.app_url or os.getenv("APP_DATABASE_URL")
    
    if not gamma_url:
        print("\n" + "=" * 60)
        print("Gamma Database Connection String Required")
        print("=" * 60)
        print("Get it from: https://console.prisma.io/cma3c5kzl0038q5j1h9z4r895/cme7zlrnm006x4bkc59wu1p8v/cmek15taf05qs42gxzvssfpvd/settings")
        print("Go to: Settings → Connection Strings")
        gamma_url = input("\nEnter Gamma database URL: ").strip()
        if not gamma_url:
            print("Error: Gamma database URL is required")
            sys.exit(1)
    
    if not app_url:
        print("\n" + "=" * 60)
        print("App Database Connection String Required")
        print("=" * 60)
        print("Get it from: https://console.prisma.io/cma3c5kzl0038q5j1h9z4r895/cme7zlrnm006x4bkc59wu1p8v/cme7zlrnm006y4bkc88txl2tx/settings")
        print("Go to: Settings → Connection Strings")
        app_url = input("\nEnter App database URL: ").strip()
        if not app_url:
            print("Error: App database URL is required")
            sys.exit(1)
    
    return gamma_url, app_url


async def fetch_from_gamma(gamma_url):
    """Fetch all PostClassifier entries from gamma database."""
    
    print(f"Connecting to gamma database...")
    gamma_client = AudiencePrisma(datasource={"url": gamma_url, "name": "audience_db"})
    
    try:
        await gamma_client.connect()
        print("Connected to gamma database")
        
        # Fetch all PostClassifier entries using raw SQL to avoid JSON conversion issues
        result = await gamma_client.query_raw(
            'SELECT id, name, prompt, description, labels, examples, "createdAt", "updatedAt" FROM "PostClassifier"'
        )
        print(f"Found {len(result)} PostClassifier entries in gamma database")
        
        # Convert to dictionaries for easier handling
        from datetime import datetime
        entries = []
        for row in result:
            # Handle dates - could be datetime object or string
            created_at = row.get("createdAt")
            updated_at = row.get("updatedAt")
            if created_at and isinstance(created_at, datetime):
                created_at = created_at.isoformat()
            if updated_at and isinstance(updated_at, datetime):
                updated_at = updated_at.isoformat()
            
            entry = {
                "id": row["id"],
                "name": row["name"],
                "prompt": row["prompt"],
                "description": row["description"],
                "labels": row["labels"],  # Already JSON from database
                "examples": row["examples"],  # Already JSON from database
                "createdAt": created_at,
                "updatedAt": updated_at,
            }
            entries.append(entry)
            print(f"  - {row['name']} (id: {row['id']})")
        
        return entries
    finally:
        await gamma_client.disconnect()
        print("Disconnected from gamma database")


async def insert_to_app(entries, app_url):
    """Insert PostClassifier entries into app database."""
    
    if not entries:
        print("No entries to migrate")
        return
    
    print(f"\nConnecting to app database...")
    app_client = AudiencePrisma(datasource={"url": app_url, "name": "audience_db"})
    
    try:
        await app_client.connect()
        print("Connected to app database")
        
        # Check existing entries to avoid duplicates using raw SQL
        existing_result = await app_client.query_raw('SELECT id FROM "PostClassifier"')
        existing_ids = {row["id"] for row in existing_result}
        print(f"Found {len(existing_ids)} existing PostClassifier entries in app database")
        
        # Insert each entry using raw SQL to properly handle JSON
        inserted_count = 0
        skipped_count = 0
        
        for entry in entries:
            if entry["id"] in existing_ids:
                print(f"  ⚠️  Skipping {entry['name']} (id: {entry['id']}) - already exists")
                skipped_count += 1
                continue
            
            try:
                # Use execute_raw for INSERT with proper JSON handling
                import json
                from datetime import datetime
                
                # Parse dates if they're strings - ensure they're datetime objects
                created_at = entry["createdAt"]
                updated_at = entry["updatedAt"]
                if isinstance(created_at, str):
                    # Handle ISO format strings
                    created_at = created_at.replace('Z', '+00:00')
                    try:
                        created_at = datetime.fromisoformat(created_at)
                    except:
                        # Fallback to current time if parsing fails
                        created_at = datetime.now()
                elif not isinstance(created_at, datetime):
                    created_at = datetime.now()
                    
                if isinstance(updated_at, str):
                    # Handle ISO format strings
                    updated_at = updated_at.replace('Z', '+00:00')
                    try:
                        updated_at = datetime.fromisoformat(updated_at)
                    except:
                        # Fallback to current time if parsing fails
                        updated_at = datetime.now()
                elif not isinstance(updated_at, datetime):
                    updated_at = datetime.now()
                
                # Handle labels - convert JSON array to PostgreSQL text array
                labels_value = entry["labels"]
                if isinstance(labels_value, str):
                    # If it's a string, try to parse it
                    try:
                        labels_value = json.loads(labels_value)
                    except:
                        labels_value = [labels_value]
                elif not isinstance(labels_value, list):
                    labels_value = []
                
                # Handle examples - keep as JSON
                examples_json = json.dumps(entry["examples"]) if entry["examples"] else None
                
                # Use execute_raw for INSERT
                # labels is text[] in app database, examples is jsonb
                # Cast dates to timestamp in SQL
                query = '''
                    INSERT INTO "PostClassifier" (id, name, prompt, description, labels, examples, "createdAt", "updatedAt")
                    VALUES ($1, $2, $3, $4, $5::text[], $6::jsonb, $7::timestamp, $8::timestamp)
                '''
                
                # Convert datetime to ISO string for PostgreSQL
                created_at_str = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
                updated_at_str = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at)
                
                await app_client.execute_raw(
                    query,
                    entry["id"],
                    entry["name"],
                    entry["prompt"],
                    entry["description"],
                    labels_value,  # Pass as list for text[]
                    examples_json,  # Pass as JSON string for jsonb
                    created_at_str,
                    updated_at_str
                )
                print(f"  ✅ Inserted {entry['name']} (id: {entry['id']})")
                inserted_count += 1
            except Exception as e:
                print(f"  ❌ Failed to insert {entry['name']}: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"\nMigration complete:")
        print(f"  - Inserted: {inserted_count}")
        print(f"  - Skipped (already exists): {skipped_count}")
        print(f"  - Total processed: {len(entries)}")
        
    finally:
        await app_client.disconnect()
        print("Disconnected from app database")


async def main():
    """Main migration function."""
    print("=" * 60)
    print("PostClassifier Migration Script")
    print("Gamma Database → App Database")
    print("=" * 60)
    print()
    
    # Get connection strings
    gamma_url, app_url = get_connection_strings()
    
    # Fetch entries from gamma
    entries = await fetch_from_gamma(gamma_url)
    
    if not entries:
        print("No entries found in gamma database. Nothing to migrate.")
        return
    
    # Insert entries into app
    await insert_to_app(entries, app_url)
    
    print("\n" + "=" * 60)
    print("Migration finished!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

