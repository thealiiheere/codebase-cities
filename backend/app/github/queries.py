"""GraphQL queries. Values always travel as variables, never pasted into the query text."""

# GitHub's maximum page size for connections.
MAX_PAGE_SIZE = 100

# What we read about each repo (shared by the public and private repo queries).
_REPO_CONNECTION = """
      pageInfo { hasNextPage endCursor }
      nodes {
        databaseId
        name
        stargazerCount
        forkCount
        diskUsage
        createdAt
        pushedAt
        primaryLanguage { name }
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
        defaultBranchRef {
          target {
            ... on Commit { history { totalCount } }
          }
        }
      }
"""

# Repos the user owns, public and not forks, most recently pushed first. privacy: PUBLIC matters
# when someone looks up the token's own account, which would otherwise include private repos.
REPOS_QUERY = (
    """
query UserRepos($login: String!, $first: Int!, $after: String) {
  user(login: $login) {
    repositories(
      first: $first
      after: $after
      ownerAffiliations: [OWNER]
      isFork: false
      privacy: PUBLIC
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {"""
    + _REPO_CONNECTION
    + """    }
  }
}
"""
)

# The token owner's own private repos (not forks), most recently pushed first. With a GitHub
# App's user token, GitHub only returns the repos the user chose to share with the app.
VIEWER_PRIVATE_REPOS_QUERY = (
    """
query ViewerPrivateRepos($first: Int!, $after: String) {
  viewer {
    login
    repositories(
      first: $first
      after: $after
      ownerAffiliations: [OWNER]
      isFork: false
      privacy: PRIVATE
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {"""
    + _REPO_CONNECTION
    + """    }
  }
}
"""
)

CONTRIBUTION_YEARS_QUERY = """
query ContributionYears($login: String!) {
  user(login: $login) {
    contributionsCollection { contributionYears }
  }
}
"""


def build_calendar_query(year_count: int) -> str:
    """One aliased contributionsCollection per year (`y0`, `y1`, ...), since GitHub caps each
    collection at one year. Variables: `login`, plus `from{i}` and `to{i}` per year."""
    variables = ["$login: String!"]
    fields = []
    for i in range(year_count):
        variables += [f"$from{i}: DateTime!", f"$to{i}: DateTime!"]
        fields.append(
            f"    y{i}: contributionsCollection(from: $from{i}, to: $to{i}) {{\n"
            "      contributionCalendar { weeks { contributionDays { date contributionCount } } }\n"
            "    }"
        )
    return (
        f"query ContributionCalendar({', '.join(variables)}) {{\n"
        "  user(login: $login) {\n" + "\n".join(fields) + "\n  }\n}\n"
    )


def build_history_query(repo_count: int) -> str:
    """One page of default-branch commit dates for several repos at once, aliased `r0`, `r1`, ...

    Batching is the point: GitHub prices a query by its connections divided by 100 (minimum 1),
    so ten repos in one query cost 1 point instead of 10. Variables: `owner`, `since`, plus
    `name{i}` and `cursor{i}` per repo.
    """
    variables = ["$owner: String!", "$since: GitTimestamp"]
    fields = []
    for i in range(repo_count):
        variables += [f"$name{i}: String!", f"$cursor{i}: String"]
        fields.append(
            f"  r{i}: repository(owner: $owner, name: $name{i}) {{\n"
            "    defaultBranchRef { target { ... on Commit {\n"
            f"      history(first: {MAX_PAGE_SIZE}, after: $cursor{i}, since: $since) {{\n"
            "        pageInfo { hasNextPage endCursor }\n"
            "        nodes { committedDate }\n"
            "      }\n"
            "    } } }\n"
            "  }"
        )
    return f"query RepoHistories({', '.join(variables)}) {{\n" + "\n".join(fields) + "\n}\n"
