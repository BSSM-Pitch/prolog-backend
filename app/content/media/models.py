from uuid import UUID

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class ProjectImage(Base, Timestamps):
    __tablename__ = "project_images"
    __table_args__ = {"schema": "content"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    target_id: Mapped[UUID] = fk_uuid()
    kind: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(255))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
