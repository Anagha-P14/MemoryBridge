import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL is missing from .env")

engine = create_engine(database_url, pool_pre_ping=True)

with engine.connect() as connection:
    result = connection.execute(
        text("SELECT current_database(), version()")
    )
    database_name, version = result.fetchone()

    print("Connected successfully!")
    print("Database:", database_name)
    print("PostgreSQL:", version)

engine.dispose()