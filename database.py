import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
from urllib.parse import quote_plus

# -------------------------------------------------
# Load environment variables from .env file
# -------------------------------------------------
load_dotenv()

# -------------------------------------------------
# Connection string resolution (priority order):
#
#   1. DATABASE_URL is set  →  use it as-is
#      (covers local SQLite: sqlite:///./database.db)
#
#   2. All four AZURE_SQL_* vars are set  →  build Azure SQL URL
#
#   3. Neither  →  fall back to local SQLite with a warning
# -------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Explicit URL provided — use it directly (SQLite, Azure SQL, Postgres, etc.)
    print(f"[DB] Using DATABASE_URL from environment.")
    SQLALCHEMY_DATABASE_URL = DATABASE_URL

else:
    AZURE_SQL_SERVER   = os.getenv("AZURE_SQL_SERVER")
    AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")
    AZURE_SQL_USER     = os.getenv("AZURE_SQL_USER")
    AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")

    _azure_vars = {
        "AZURE_SQL_SERVER":   AZURE_SQL_SERVER,
        "AZURE_SQL_DATABASE": AZURE_SQL_DATABASE,
        "AZURE_SQL_USER":     AZURE_SQL_USER,
        "AZURE_SQL_PASSWORD": AZURE_SQL_PASSWORD,
    }
    _missing = [k for k, v in _azure_vars.items() if not v]

    

if not _missing:
    print("[DB] All AZURE_SQL_* vars found. Connecting to Azure SQL...")
    
    odbc_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server=tcp:{AZURE_SQL_SERVER},1433;"
        f"Database={AZURE_SQL_DATABASE};"
        f"Uid={AZURE_SQL_USER};"
        f"Pwd={AZURE_SQL_PASSWORD};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )
    SQLALCHEMY_DATABASE_URL = f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"

# -------------------------------------------------
# SQLAlchemy engine — SQLite needs check_same_thread=False
# -------------------------------------------------
is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=not is_sqlite,   # pyodbc supports this; SQLite doesn't need it
    **({"pool_size": 5, "max_overflow": 10} if not is_sqlite else {}),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# -------------------------------------------------
# Dependency for FastAPI routes
# -------------------------------------------------
def get_db():
    """Provide a transactional database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
