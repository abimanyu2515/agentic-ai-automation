from contextlib import contextmanager
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config.settings import get_settings

logger = logging.getLogger("backend.database.db")

settings = get_settings()
provider = settings.repository.provider
db_url = settings.repository.database_url

if provider == "json":
    # Fallback to a local SQLite database for DB dependencies to avoid connection failures
    db_url = "sqlite:///./backend/database/local_fallback.db"

# Set up engine arguments based on driver
engine_kwargs = {}
if "sqlite" in db_url:
    # SQLite does not support pool_size or max_overflow
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({
        "pool_size": settings.repository.pool_size,
        "max_overflow": settings.repository.max_overflow,
        "pool_timeout": settings.repository.pool_timeout,
        "pool_pre_ping": True
    })

logger.info(f"Initializing database engine with URL: {db_url}")
engine = create_engine(db_url, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Enforce foreign key constraints dynamically for SQLite
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in db_url:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            logger.info("SQLite foreign_keys PRAGMA enabled")
        except Exception as e:
            logger.warning(f"Failed to enable SQLite foreign keys: {e}")
        finally:
            cursor.close()

# Declarative Base for models
Base = declarative_base()

@contextmanager
def get_db_session():
    """Context manager for managing database session lifecycle."""
    session = SessionLocal()
    try:
        yield session
    except Exception as e:
        logger.error(f"Database error occurred: {e}. Rolling back transaction.")
        session.rollback()
        raise
    finally:
        session.close()
