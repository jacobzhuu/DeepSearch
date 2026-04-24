from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import CandidateUrl, SearchQuery
from packages.db.repositories.base import SQLAlchemyRepository


class SearchQueryRepository(SQLAlchemyRepository[SearchQuery]):
    model = SearchQuery

    def list_for_task(self, task_id: UUID) -> list[SearchQuery]:
        statement = (
            select(SearchQuery)
            .where(SearchQuery.task_id == task_id)
            .order_by(SearchQuery.issued_at.asc(), SearchQuery.id.asc())
        )
        return list(self.session.scalars(statement))

    def list_for_run(self, run_id: UUID) -> list[SearchQuery]:
        statement = (
            select(SearchQuery)
            .where(SearchQuery.run_id == run_id)
            .order_by(SearchQuery.issued_at.asc(), SearchQuery.id.asc())
        )
        return list(self.session.scalars(statement))


class CandidateUrlRepository(SQLAlchemyRepository[CandidateUrl]):
    model = CandidateUrl

    def list_by_ids_for_task(
        self,
        task_id: UUID,
        candidate_url_ids: list[UUID],
    ) -> list[CandidateUrl]:
        if not candidate_url_ids:
            return []
        statement = (
            select(CandidateUrl)
            .where(
                CandidateUrl.task_id == task_id,
                CandidateUrl.id.in_(candidate_url_ids),
            )
            .order_by(CandidateUrl.id.asc())
        )
        return list(self.session.scalars(statement))

    def get_for_task_canonical_url(
        self,
        task_id: UUID,
        canonical_url: str,
    ) -> CandidateUrl | None:
        statement = select(CandidateUrl).where(
            CandidateUrl.task_id == task_id,
            CandidateUrl.canonical_url == canonical_url,
        )
        return self.session.scalar(statement)

    def list_for_task(
        self,
        task_id: UUID,
        *,
        domain: str | None = None,
        selected: bool | None = None,
        limit: int | None = None,
    ) -> list[CandidateUrl]:
        statement = (
            select(CandidateUrl)
            .join(SearchQuery, SearchQuery.id == CandidateUrl.search_query_id)
            .where(CandidateUrl.task_id == task_id)
        )
        if domain is not None:
            statement = statement.where(CandidateUrl.domain == domain)
        if selected is not None:
            statement = statement.where(CandidateUrl.selected == selected)
        statement = statement.order_by(
            SearchQuery.issued_at.asc(),
            CandidateUrl.rank.asc(),
            CandidateUrl.id.asc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))

    def list_for_search_query(self, search_query_id: UUID) -> list[CandidateUrl]:
        statement = (
            select(CandidateUrl)
            .where(CandidateUrl.search_query_id == search_query_id)
            .order_by(CandidateUrl.rank.asc(), CandidateUrl.id.asc())
        )
        return list(self.session.scalars(statement))
