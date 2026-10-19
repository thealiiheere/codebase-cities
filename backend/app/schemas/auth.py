from pydantic import BaseModel

from app.github.models import UserProfile


class AuthUser(BaseModel):
    login: str
    name: str | None
    avatar_url: str

    @classmethod
    def from_profile(cls, profile: UserProfile) -> "AuthUser":
        return cls(login=profile.login, name=profile.name, avatar_url=profile.avatar_url)


class PrivateRepos(BaseModel):
    # GitHub's page where the user picks which private repos to share with the site.
    install_url: str


class AuthStatus(BaseModel):
    # False when this server has no GitHub OAuth App configured: the page offers no sign-in.
    enabled: bool
    # The signed-in visitor, or null.
    user: AuthUser | None
    # Set when the visitor is signed in and the server can show private repos.
    private_repos: PrivateRepos | None = None
