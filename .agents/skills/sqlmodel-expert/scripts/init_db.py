#!/usr/bin/env python3
"""
Initialize database with SQLModel models and create tables
"""
from sqlmodel import SQLModel, create_engine
import os
import sys


def init_database(database_url: str = None):
    """
    Initialize database and create all tables

    Args:
        database_url: Database connection string
                     If None, reads from DATABASE_URL environment variable
    """
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("Error: DATABASE_URL not provided")
        sys.exit(1)

    print(f"Connecting to: {database_url.split('@')[1] if '@' in database_url else database_url}")

    # Create engine
    engine = create_engine(database_url, echo=True)

    # Import all models (ensure they're registered with SQLModel.metadata)
    try:
        from app.models import *  # Import all your models
    except ImportError:
        print("Warning: Could not import models from app.models")
        print("Make sure your models are imported before creating tables")

    # Create tables
    print("\nCreating tables...")
    SQLModel.metadata.create_all(engine)
    print("✅ Database initialized successfully!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize database with SQLModel")
    parser.add_argument(
        "--url",
        help="Database URL (default: from DATABASE_URL env var)"
    )

    args = parser.parse_args()
    init_database(args.url)
