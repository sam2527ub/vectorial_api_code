"""
Database backup utility for audience database.
Exports all tables to CSV files with timestamp.
READ-ONLY OPERATION - Does not modify the database.
"""
import os
import csv
import logging
from datetime import datetime
from typing import List
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


def get_all_tables(conn) -> List[str]:
    """Get all table names from the database (read-only)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tablename 
            FROM pg_tables 
            WHERE schemaname = 'public'
            ORDER BY tablename;
        """)
        return [row[0] for row in cur.fetchall()]


def export_table_to_csv(conn, table_name: str, output_dir: Path) -> str:
    """Export a single table to CSV (read-only SELECT only)."""
    csv_path = output_dir / f"{table_name}.csv"
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Get column names
        cur.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}' 
            AND table_schema = 'public'
            ORDER BY ordinal_position;
        """)
        columns = [row['column_name'] for row in cur.fetchall()]
        
        # Fetch all data (READ-ONLY - just SELECT)
        cur.execute(f'SELECT * FROM "{table_name}"')
        rows = cur.fetchall()
        
        # Write to CSV
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            if rows:
                writer = csv.DictWriter(csvfile, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    # Convert dict-like rows to regular dicts
                    row_dict = dict(row)
                    # Handle JSON fields and special types
                    for key, value in row_dict.items():
                        if value is None:
                            row_dict[key] = ''
                        elif isinstance(value, (dict, list)):
                            import json
                            row_dict[key] = json.dumps(value)
                    writer.writerow(row_dict)
            else:
                # Empty table - just write headers
                writer = csv.DictWriter(csvfile, fieldnames=columns)
                writer.writeheader()
    
    logger.info(f"Exported {len(rows)} rows from {table_name} to {csv_path}")
    return str(csv_path)


def backup_audience_database_to_csv(output_dir: str = None) -> str:
    """
    Backup all tables from audience database to CSV files.
    READ-ONLY OPERATION - Does not modify the database.
    
    Args:
        output_dir: Directory to save CSV files. Defaults to ~/Desktop/backup/csv_{timestamp}/
    
    Returns:
        Path to the backup directory
    """
    database_url = os.getenv("AUDIENCE_DATABASE_URL")
    if not database_url:
        raise ValueError("AUDIENCE_DATABASE_URL environment variable not set")
    
    # Create backup directory with timestamp on Desktop
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop_path = Path.home() / "Desktop" / "backup"
    
    if output_dir is None:
        output_dir = desktop_path / f"csv_{timestamp}"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting CSV backup to {output_dir}")
    print(f"📁 CSV backup location: {output_dir}")
    
    # Connect to database (read-only operations only)
    conn = psycopg2.connect(database_url)
    
    try:
        # Get all tables (read-only)
        tables = get_all_tables(conn)
        logger.info(f"Found {len(tables)} tables: {', '.join(tables)}")
        print(f"📊 Found {len(tables)} tables to backup")
        
        # Export each table (read-only SELECT)
        exported_files = []
        for i, table in enumerate(tables, 1):
            try:
                print(f"  [{i}/{len(tables)}] Exporting {table}...")
                csv_path = export_table_to_csv(conn, table, output_dir)
                exported_files.append(csv_path)
            except Exception as e:
                logger.error(f"Failed to export table {table}: {e}")
                print(f"  ❌ Failed to export {table}: {e}")
        
        # Save metadata
        metadata_path = output_dir / "_backup_metadata.txt"
        with open(metadata_path, 'w') as f:
            f.write(f"CSV Backup created: {datetime.now().isoformat()}\n")
            f.write(f"Database: {database_url.split('@')[1] if '@' in database_url else 'hidden'}\n")
            f.write(f"Total tables: {len(tables)}\n")
            f.write(f"Exported files: {len(exported_files)}\n\n")
            f.write("Tables:\n")
            for table in tables:
                f.write(f"  - {table}\n")
        
        logger.info(f"CSV backup completed! {len(exported_files)} files saved to {output_dir}")
        print(f"\n✅ CSV backup completed! {len(exported_files)} files saved")
        return str(output_dir)
        
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("\n🔄 Starting CSV backup (READ-ONLY operation)...\n")
    try:
        backup_dir = backup_audience_database_to_csv()
        print(f"\n✅ CSV Backup completed successfully!")
        print(f"📁 Backup location: {backup_dir}\n")
    except Exception as e:
        print(f"\n❌ Backup failed: {e}\n")
        raise

