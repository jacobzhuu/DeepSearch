from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class SQLAlchemyRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        self.session.flush()
        return instance

    def get(self, entity_id: UUID) -> ModelT | None:
        return self.session.get(self.model, entity_id)
