"""
pg_dump backup utility for audience database.
READ-ONLY OPERATION - Does not modify the database.
"""
import os
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import urllib.parse

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def parse_database_url(url: str) -> dict:
    """Parse PostgreSQL database URL into components."""
    parsed = urllib.parse.urlparse(url)
    return {
        'user': parsed.username,
        'password': parsed.password,
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/').split('?')[0]
    }


def backup_audience_database_pg_dump(output_dir: str = None) -> str:
    """
    Backup audience database using pg_dump.
    READ-ONLY OPERATION - Does not modify the database.
    
    Args:
        output_dir: Directory to save backup. Defaults to ~/Desktop/backup/pg_dump_{timestamp}/
    
    Returns:
        Path to the backup directory
    """
    database_url = os.getenv("AUDIENCE_DATABASE_URL")
    if not database_url:
        raise ValueError("AUDIENCE_DATABASE_URL environment variable not set")
    
    # Parse database URL
    db_params = parse_database_url(database_url)
    
    # Create backup directory with timestamp on Desktop
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop_path = Path.home() / "Desktop" / "backup"
    
    if output_dir is None:
        output_dir = desktop_path / f"pg_dump_{timestamp}"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting pg_dump backup to {output_dir}")
    print(f"📁 pg_dump backup location: {output_dir}")
    
    # Backup filename
    backup_file = output_dir / "audience_db_backup.sql"
    
    print(f"\n📊 Database Info:")
    print(f"   Host: {db_params['host']}")
    print(f"   Port: {db_params['port']}")
    print(f"   Database: {db_params['database']}")
    print(f"   User: {db_params['user']}")
    print()
    
    # Set PGPASSWORD environment variable for pg_dump
    env = os.environ.copy()
    env['PGPASSWORD'] = db_params['password']
    
    # Run pg_dump
    print("⏳ Creating backup...")
    try:
        result = subprocess.run(
            [
                'pg_dump',
                '-h', db_params['host'],
                '-p', str(db_params['port']),
                '-U', db_params['user'],
                '-d', db_params['database'],
                '--clean',
                '--if-exists',
                '--format=plain',
                '--verbose',
                '-f', str(backup_file)
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Get file size
        file_size = backup_file.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        
        print(f"✅ Backup completed successfully!")
        print(f"📁 Backup file: {backup_file}")
        print(f"📦 File size: {file_size_mb:.2f} MB")
        
        # Create compressed version
        print()
        print("⏳ Creating compressed backup...")
        import gzip
        import shutil
        
        compressed_file = backup_file.with_suffix('.sql.gz')
        with open(backup_file, 'rb') as f_in:
            with gzip.open(compressed_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        
        compressed_size = compressed_file.stat().st_size
        compressed_size_mb = compressed_size / (1024 * 1024)
        print(f"✅ Compressed backup: {compressed_file} ({compressed_size_mb:.2f} MB)")
        
        # Save metadata
        metadata_file = output_dir / "_backup_metadata.txt"
        with open(metadata_file, 'w') as f:
            f.write(f"pg_dump Backup created: {datetime.now().isoformat()}\n")
            f.write(f"Database Host: {db_params['host']}:{db_params['port']}\n")
            f.write(f"Database Name: {db_params['database']}\n")
            f.write(f"Backup File: {backup_file.name}\n")
            f.write(f"Compressed File: {compressed_file.name}\n")
            f.write(f"Backup Size: {file_size_mb:.2f} MB\n")
            f.write(f"Compressed Size: {compressed_size_mb:.2f} MB\n")
            f.write("\nTo restore this backup:\n")
            f.write(f"  psql -h {db_params['host']} -p {db_params['port']} -U {db_params['user']} -d {db_params['database']} -f \"{backup_file}\"\n")
            f.write("\nOr for compressed backup:\n")
            f.write(f"  gunzip -c \"{compressed_file}\" | psql -h {db_params['host']} -p {db_params['port']} -U {db_params['user']} -d {db_params['database']}\n")
        
        print()
        print(f"📄 Metadata saved to: {metadata_file}")
        print()
        print("✅ pg_dump backup completed!")
        
        return str(output_dir)
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ pg_dump failed!")
        print(f"Error output: {e.stderr}")
        raise
    except FileNotFoundError:
        raise ValueError("pg_dump command not found. Please install PostgreSQL client tools.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("\n🔄 Starting pg_dump backup (READ-ONLY operation)...\n")
    try:
        backup_dir = backup_audience_database_pg_dump()
        print(f"\n✅ pg_dump Backup completed successfully!")
        print(f"📁 Backup location: {backup_dir}\n")
    except Exception as e:
        print(f"\n❌ Backup failed: {e}\n")
        raise





