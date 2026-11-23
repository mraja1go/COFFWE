"""Shared SQLAlchemy plumbing: the declarative Base, engine, and session factory."""

import os

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()  # reads .env into os.environ

# Predictable constraint names (e.g. "pk_blocks", "uq_blocks_code").
# Without this, Postgres invents names, and later migrations that need to
# drop or alter a constraint can't reliably refer to it.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_engine(os.environ["DATABASE_URL"], echo=False)
SessionLocal = sessionmaker(bind=engine)
