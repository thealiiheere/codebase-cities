from pydantic import BaseModel

from app.github.models import UserProfile


class AuthUser(BaseModel):
    login: str
    name: str | None
    avatar_url: str

    @classmethod
    def from_profile(cls, profile: UserProfile) -> "AuthUser":
        return cls(login=profile.login, name=profile.name, avatar_url=profile.avatar_url)


class AuthStatus(BaseModel):
    # False when this server has no GitHub OAuth App configured: the page offers no sign-in.
    enabled: bool
    # The signed-in visitor, or null.
    user: AuthUser | None
