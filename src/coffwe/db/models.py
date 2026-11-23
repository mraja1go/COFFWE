"""ORM models. Placeholder for now; the real estate schema comes next."""

from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from coffwe.db.base import Base


class Block(Base):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # e.g. "A1"
    name: Mapped[str] = mapped_column(String(100))
    area_acres: Mapped[Decimal] = mapped_column(Numeric(5, 2))
