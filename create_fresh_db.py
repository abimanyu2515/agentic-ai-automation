import sys
from pathlib import Path
 
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
 
from backend.config.settings import get_settings
from backend.database.db import engine, Base, SessionLocal
from backend.database import db_models  # noqa: F401  registers every table on Base.metadata
from backend.database.db_seeder import seed_database
 
ALEMBIC_INI = Path(__file__).resolve().parent / "alembic.ini"
 
 
def main():
    settings = get_settings()
    if settings.repository.provider != "postgres":
        print(
            f"REPOSITORY_PROVIDER is '{settings.repository.provider}'. "
            "Set REPOSITORY_PROVIDER=postgres and DATABASE_URL in .env first."
        )
        sys.exit(1)
 
    existing = inspect(engine).get_table_names()
    if existing:
        print(f"Database is not empty ({len(existing)} tables found). Use a new database or run: alembic upgrade head")
        sys.exit(1)
 
    print("Creating all tables...")
    Base.metadata.create_all(bind=engine)
    print(f"Tables created: {', '.join(sorted(Base.metadata.tables))}")
 
    print("Seeding auth data (roles, permissions, dev users)...")
    db = SessionLocal()
    try:
        seed_database(db)
        print("Auth data seeded.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
 
    print("Stamping Alembic revision at head...")
    command.stamp(Config(str(ALEMBIC_INI)), "head")
    print("Alembic stamped.")
 
    print("\nDone. Fresh database ready.")
    print("Start API: uvicorn backend.main:app --reload --port 8000")
 
 
if __name__ == "__main__":
    main()