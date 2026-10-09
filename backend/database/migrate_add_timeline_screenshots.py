"""Add timeline and screenshots columns to executions table"""
import os
import re
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def parse_database_url(db_url):
    """Parse SQLAlchemy DATABASE_URL into psycopg2 compatible format"""
    # Handle postgresql+psycopg2:// format
    if db_url.startswith("postgresql+psycopg2://"):
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")
    elif db_url.startswith("postgresql://"):
        pass  # Already in correct format
    
    return db_url

def migrate():
    """Add timeline and screenshots columns to executions table"""
    # Get database connection details from environment
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not found in environment variables")
        return
    
    try:
        # Parse connection string to psycopg2 format
        parsed_url = parse_database_url(db_url)
        print(f"Connecting to database...")
        
        conn = psycopg2.connect(parsed_url)
        cursor = conn.cursor()
        
        try:
            # Add timeline column
            cursor.execute("ALTER TABLE executions ADD COLUMN IF NOT EXISTS timeline JSON")
            print("Added timeline column to executions table")
        except Exception as e:
            print(f"Error adding timeline column: {e}")
        
        try:
            # Add screenshots column
            cursor.execute("ALTER TABLE executions ADD COLUMN IF NOT EXISTS screenshots JSON")
            print("Added screenshots column to executions table")
        except Exception as e:
            print(f"Error adding screenshots column: {e}")
        
        conn.commit()
        print("Migration completed successfully")
        
    except Exception as e:
        print(f"Database connection error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    migrate()
