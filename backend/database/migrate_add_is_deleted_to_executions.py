"""
Migration script to add is_deleted, deleted_at, deleted_by columns to executions table.
Run this script to update the database schema.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database.db import engine, SessionLocal

def migrate():
    """Add is_deleted, deleted_at, deleted_by columns to executions table."""
    session = SessionLocal()
    
    try:
        # Check if columns already exist
        check_sql = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'executions' 
            AND column_name IN ('is_deleted', 'deleted_at', 'deleted_by')
        """)
        result = session.execute(check_sql).fetchall()
        
        if len(result) >= 3:
            print("Columns already exist in executions table. Skipping migration.")
            return
        
        # Add columns
        print("Adding is_deleted column to executions table...")
        session.execute(text("ALTER TABLE executions ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE NOT NULL"))
        
        print("Adding deleted_at column to executions table...")
        session.execute(text("ALTER TABLE executions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE"))
        
        print("Adding deleted_by column to executions table...")
        session.execute(text("ALTER TABLE executions ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES users(id) ON DELETE SET NULL"))
        
        # Create index on is_deleted
        print("Creating index on is_deleted...")
        session.execute(text("CREATE INDEX IF NOT EXISTS ix_executions_is_deleted ON executions(is_deleted)"))
        
        session.commit()
        print("Migration completed successfully!")
        
    except Exception as e:
        session.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    migrate()
