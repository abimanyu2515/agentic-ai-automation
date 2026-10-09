"""
Simple script to delete the top 2 failed executions for a project.
Uses psycopg2 directly to avoid import issues.
"""

import psycopg2
from datetime import datetime

# Database connection - from settings.py default
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "agentic_ai"
DB_USER = "postgres"
DB_PASSWORD = "scoot"

PROJECT_ID = "7f65154f-9e54-4bad-8e8f-4904f4772612"

def delete_top_failed_executions():
    """Delete the top 2 failed executions for the project."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # First, get the top 2 failed execution IDs
        select_sql = """
            SELECT id 
            FROM executions 
            WHERE project_id = %s AND status = 'failed'
            ORDER BY executed_at DESC 
            LIMIT 2
        """
        cursor.execute(select_sql, (PROJECT_ID,))
        result = cursor.fetchall()
        
        if not result:
            print("No failed executions found for this project.")
            return
        
        execution_ids = [row[0] for row in result]
        print(f"Found {len(execution_ids)} failed executions to delete:")
        for eid in execution_ids:
            print(f"  - {eid}")
        
        # Delete them
        delete_sql = "DELETE FROM executions WHERE id = %s"
        
        for eid in execution_ids:
            cursor.execute(delete_sql, (eid,))
            conn.commit()
            print(f"Deleted execution {eid}")
        
        print(f"Successfully deleted {len(execution_ids)} failed executions.")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        if conn:
            conn.rollback()
            conn.close()

if __name__ == "__main__":
    delete_top_failed_executions()
