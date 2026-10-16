"""Pydantic models of the JSON GitHub sends, and their conversion to our output types.

Validating responses here means a change in GitHub's API fails loudly at the edge instead of
quietly storing bad data. GraphQL fields are camelCase; `to_camel` maps them to snake_case.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.github.models import ContributionDay, RepoInfo, UserProfile


class _GraphQLPayload(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class Named(_GraphQLPayload):
    name: str


class PageInfo(_GraphQLPayload):
    has_next_page: bool
    end_cursor: str | None = None


# --- Repos -------------------------------------------------------------------------------------


class LanguageEdge(_GraphQLPayload):
    size: int
    node: Named


class LanguageConnection(_GraphQLPayload):
    edges: list[LanguageEdge]


class TotalCount(_GraphQLPayload):
    total_count: int


class CommitTotalTarget(_GraphQLPayload):
    history: TotalCount | None = None


class CommitTotalBranch(_GraphQLPayload):
    target: CommitTotalTarget | None = None


class RepoNode(_GraphQLPayload):
    database_id: int
    name: str
    stargazer_count: int
    fork_count: int
    disk_usage: int | None = None
    created_at: dt.datetime
    pushed_at: dt.datetime | None = None
    primary_language: Named | None = None
    languages: LanguageConnection
    # Null for an empty repo with no commits yet.
    default_branch_ref: CommitTotalBranch | None = None

    def to_repo_info(self) -> RepoInfo:
        target = self.default_branch_ref.target if self.default_branch_ref else None
        history = target.history if target else None
        return RepoInfo(
            github_id=self.database_id,
            name=self.name,
            stars=self.stargazer_count,
            forks=self.fork_count,
            size_kb=self.disk_usage or 0,
            primary_language=self.primary_language.name if self.primary_language else None,
            languages={edge.node.name: edge.size for edge in self.languages.edges},
            created_at=self.created_at,
            pushed_at=self.pushed_at,
            total_commits=history.total_count if history else 0,
        )


class RepoConnection(_GraphQLPayload):
    page_info: PageInfo
    nodes: list[RepoNode]


class UserRepos(_GraphQLPayload):
    repositories: RepoConnection


# --- Contributions calendar --------------------------------------------------------------------


class ContributionDayNode(_GraphQLPayload):
    date: dt.date
    contribution_count: int

    def to_contribution_day(self) -> ContributionDay:
        return ContributionDay(date=self.date, count=self.contribution_count)


class ContributionWeek(_GraphQLPayload):
    contribution_days: list[ContributionDayNode]


class ContributionCalendar(_GraphQLPayload):
    weeks: list[ContributionWeek]


class ContributionsCollection(_GraphQLPayload):
    contribution_calendar: ContributionCalendar


class ContributionYears(_GraphQLPayload):
    contribution_years: list[int]


class UserContributionYears(_GraphQLPayload):
    contributions_collection: ContributionYears


# --- Commit history ----------------------------------------------------------------------------


class CommitNode(_GraphQLPayload):
    committed_date: dt.datetime


class HistoryPage(_GraphQLPayload):
    page_info: PageInfo
    nodes: list[CommitNode]


class HistoryTarget(_GraphQLPayload):
    history: HistoryPage | None = None


class HistoryBranch(_GraphQLPayload):
    target: HistoryTarget | None = None


class RepoHistory(_GraphQLPayload):
    default_branch_ref: HistoryBranch | None = None

    @property
    def page(self) -> HistoryPage | None:
        target = self.default_branch_ref.target if self.default_branch_ref else None
        return target.history if target else None


# --- REST --------------------------------------------------------------------------------------


class RestUser(BaseModel):
    """GET /users/{login}. REST already uses snake_case."""

    id: int
    login: str
    name: str | None = None
    avatar_url: str
    created_at: dt.datetime
    type: str  # "User" or "Organization"

    def to_profile(self) -> UserProfile:
        return UserProfile(
            github_id=self.id,
            login=self.login,
            name=self.name,
            avatar_url=self.avatar_url,
            created_at=self.created_at,
        )
