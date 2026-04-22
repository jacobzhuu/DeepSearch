from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import CandidateUrl, SearchQuery
from packages.db.repositories.base import SQLAlchemyRepository


class SearchQueryRepository(SQLAlchemyRepository[SearchQuery]):
    model = SearchQuery

    def list_for_run(self, run_id: UUID) -> list[SearchQuery]:
        statement = (
            select(SearchQuery)
            .where(SearchQuery.run_id == run_id)
            .order_by(SearchQuery.issued_at.asc(), SearchQuery.id.asc())
        )
        return list(self.session.scalars(statement))


class CandidateUrlRepository(SQLAlchemyRepository[CandidateUrl]):
    model = CandidateUrl

    def list_for_search_query(self, search_query_id: UUID) -> list[CandidateUrl]:
        statement = (
            select(CandidateUrl)
            .where(CandidateUrl.search_query_id == search_query_id)
            .order_by(CandidateUrl.rank.asc(), CandidateUrl.id.asc())
        )
        return list(self.session.scalars(statement))
