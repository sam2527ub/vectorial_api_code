"""
SQL dump backup utility for audience database.
Creates a SQL file with CREATE TABLE and INSERT statements.
READ-ONLY OPERATION - Does not modify the database.
"""
import os
import json
import logging
import gzip
from datetime import datetime
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def get_all_tables(conn) -> list:
    """Get all table names from the database (read-only)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tablename 
            FROM pg_tables 
            WHERE schemaname = 'public'
            ORDER BY tablename;
        """)
        return [row[0] for row in cur.fetchall()]


def get_table_schema(conn, table_name: str) -> str:
    """Get CREATE TABLE statement for a table."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT column_name, data_type, character_maximum_length, 
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table_name}' 
            AND table_schema = 'public'
            ORDER BY ordinal_position;
        """)
        columns = cur.fetchall()
        
        # Build CREATE TABLE statement (simplified)
        schema_lines = [f'CREATE TABLE IF NOT EXISTS "{table_name}" (']
        col_defs = []
        
        for col in columns:
            col_name, data_type, max_length, is_nullable, default = col
            col_def = f'  "{col_name}" {data_type}'
            if max_length:
                col_def += f'({max_length})'
            if is_nullable == 'NO':
                col_def += ' NOT NULL'
            if default:
                col_def += f' DEFAULT {default}'
            col_defs.append(col_def)
        
        schema_lines.append(',\n'.join(col_defs))
        schema_lines.append(');')
        
        return '\n'.join(schema_lines)


def escape_sql_value(value) -> str:
    """Escape a value for SQL INSERT statement."""
    if value is None:
        return 'NULL'
    elif isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, (dict, list)):
        # JSON fields
        return "'" + json.dumps(value).replace("'", "''") + "'"
    else:
        # String value
        value_str = str(value).replace("'", "''").replace("\\", "\\\\")
        return f"'{value_str}'"


def dump_table_to_sql(conn, table_name: str, sql_file) -> int:
    """Dump table data to SQL file with INSERT statements."""
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
        
        # Fetch all data
        cur.execute(f'SELECT * FROM "{table_name}"')
        rows = cur.fetchall()
        
        if not rows:
            logger.info(f"Table {table_name} is empty")
            return 0
        
        # Write DELETE and INSERT statements
        sql_file.write(f'\n-- Data for table "{table_name}"\n')
        sql_file.write(f'DELETE FROM "{table_name}";\n')
        
        # Write INSERT statements in batches
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            values_list = []
            
            for row in batch:
                row_dict = dict(row)
                values = [escape_sql_value(row_dict.get(col)) for col in columns]
                values_list.append(f'({", ".join(values)})')
            
            columns_str = ', '.join([f'"{col}"' for col in columns])
            sql_file.write(f'INSERT INTO "{table_name}" ({columns_str}) VALUES\n')
            sql_file.write(',\n'.join(values_list))
            sql_file.write(';\n\n')
        
        logger.info(f"Dumped {len(rows)} rows from {table_name}")
        return len(rows)


def backup_audience_database_sql_dump(output_dir: str = None) -> str:
    """
    Backup all tables from audience database to SQL file.
    READ-ONLY OPERATION - Does not modify the database.
    
    Args:
        output_dir: Directory to save SQL file. Defaults to ~/Desktop/backup/sql_{timestamp}/
    
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
        output_dir = desktop_path / f"sql_{timestamp}"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting SQL dump backup to {output_dir}")
    print(f"📁 SQL dump backup location: {output_dir}")
    
    # Connect to database
    conn = psycopg2.connect(database_url)
    
    try:
        # Get all tables
        tables = get_all_tables(conn)
        logger.info(f"Found {len(tables)} tables: {', '.join(tables)}")
        print(f"📊 Found {len(tables)} tables to backup")
        
        # Create SQL file
        sql_file_path = output_dir / "audience_db_backup.sql"
        
        with open(sql_file_path, 'w', encoding='utf-8') as sql_file:
            # Write header
            sql_file.write(f"""-- SQL Dump Backup
-- Created: {datetime.now().isoformat()}
-- Database: {database_url.split('@')[1] if '@' in database_url else 'hidden'}
-- Total Tables: {len(tables)}

BEGIN;

""")
            
            # Dump each table
            total_rows = 0
            for i, table in enumerate(tables, 1):
                try:
                    print(f"  [{i}/{len(tables)}] Dumping {table}...")
                    rows = dump_table_to_sql(conn, table, sql_file)
                    total_rows += rows
                except Exception as e:
                    logger.error(f"Failed to dump table {table}: {e}")
                    print(f"  ❌ Failed to dump {table}: {e}")
                    sql_file.write(f'-- Error dumping table {table}: {e}\n\n')
            
            # Write footer
            sql_file.write("COMMIT;\n")
        
        # Get file size
        file_size = sql_file_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        
        # Create compressed version
        print()
        print("⏳ Creating compressed backup...")
        compressed_file = sql_file_path.with_suffix('.sql.gz')
        with open(sql_file_path, 'rb') as f_in:
            with gzip.open(compressed_file, 'wb') as f_out:
                f_out.writelines(f_in)
        
        compressed_size = compressed_file.stat().st_size
        compressed_size_mb = compressed_size / (1024 * 1024)
        
        # Save metadata
        metadata_file = output_dir / "_backup_metadata.txt"
        with open(metadata_file, 'w') as f:
            f.write(f"SQL Dump Backup created: {datetime.now().isoformat()}\n")
            f.write(f"Database: {database_url.split('@')[1] if '@' in database_url else 'hidden'}\n")
            f.write(f"Total tables: {len(tables)}\n")
            f.write(f"Total rows: {total_rows}\n")
            f.write(f"Backup File: {sql_file_path.name}\n")
            f.write(f"Compressed File: {compressed_file.name}\n")
            f.write(f"Backup Size: {file_size_mb:.2f} MB\n")
            f.write(f"Compressed Size: {compressed_size_mb:.2f} MB\n")
            f.write("\nTo restore this backup:\n")
            f.write(f"  psql -d DATABASE -f \"{sql_file_path}\"\n")
            f.write("\nOr for compressed backup:\n")
            f.write(f"  gunzip -c \"{compressed_file}\" | psql -d DATABASE\n")
        
        print(f"✅ SQL dump completed!")
        print(f"📁 Backup file: {sql_file_path} ({file_size_mb:.2f} MB)")
        print(f"📦 Compressed file: {compressed_file} ({compressed_size_mb:.2f} MB)")
        print(f"📄 Metadata: {metadata_file}")
        print(f"📊 Total rows exported: {total_rows}")
        
        logger.info(f"SQL dump backup completed! {total_rows} rows exported to {output_dir}")
        return str(output_dir)
        
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("\n🔄 Starting SQL dump backup (READ-ONLY operation)...\n")
    try:
        backup_dir = backup_audience_database_sql_dump()
        print(f"\n✅ SQL Dump Backup completed successfully!")
        print(f"📁 Backup location: {backup_dir}\n")
    except Exception as e:
        print(f"\n❌ Backup failed: {e}\n")
        raise






