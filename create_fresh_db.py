#!/usr/bin/env python
"""Create a fresh PostgreSQL database with all tables and seed auth data.

Usage:
    1. Create database: psql -U postgres -c "CREATE DATABASE agentic_ai_fresh;"
    2. Set .env: DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_ai_fresh
                 REPOSITORY_PROVIDER=postgres
    3. Run: python create_fresh_db.py
"""

from backend.database.db import engine, Base
from backend.database.db_seeder import seed_database
from backend.database.db import SessionLocal

def main():
    print("Creating all tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created.")

    print("Seeding auth data (roles, permissions, dev users)...")
    db = SessionLocal()
    try:
        seed_database(db)
        print("Auth data seeded.")
    finally:
        db.close()

    print("\nDone. Fresh database ready.")
    print("Start API: uvicorn backend.main:app --reload --port 8000")

if __name__ == "__main__":
    main()