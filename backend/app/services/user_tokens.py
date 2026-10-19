"""A signed-in user's GitHub tokens: kept encrypted so their private repos can be fetched in the
background, renewed when they expire, and forgotten (with the private repos) on sign-out."""

import datetime as dt
import logging

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import PrivateAccessError
from app.github.oauth import GitHubOAuth, OAuthError, UserToken
from app.ingestion.store import delete_private_repos
from app.models import StoredToken
from app.token_cipher import TokenCipher, TokenDecryptError

logger = logging.getLogger(__name__)

# Renew a little before the access token expires, so it can't run out in the middle of a job.
RENEW_BEFORE = dt.timedelta(minutes=10)
SIGN_IN_AGAIN = "Sign in with GitHub again to update your private repos."


async def keep_token(
    session: AsyncSession, cipher: TokenCipher | None, github_id: int, token: UserToken
) -> None:
    """Store (or replace) a user's tokens, encrypted. Does nothing when private repos are off."""
    if cipher is None:
        return
    values = {
        "github_id": github_id,
        "access_token": cipher.encrypt(token.access_token),
        "access_expires_at": token.access_expires_at,
        "refresh_token": cipher.encrypt(token.refresh_token) if token.refresh_token else None,
        "refresh_expires_at": token.refresh_expires_at,
        "updated_at": dt.datetime.now(dt.UTC),
    }
    statement = pg_insert(StoredToken).values(values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[StoredToken.github_id],
            set_={column: statement.excluded[column] for column in values if column != "github_id"},
        )
    )
    await session.commit()


async def remember_sign_in(
    session: AsyncSession, cipher: TokenCipher | None, github_id: int, token: UserToken
) -> None:
    """keep_token for a fresh sign-in, which must succeed even if storing the token fails (the
    user then just can't add private repos until they sign in again)."""
    try:
        await keep_token(session, cipher, github_id, token)
    except Exception:
        logger.warning("Couldn't store the GitHub token of user %s", github_id, exc_info=True)


async def has_token(session: AsyncSession, github_id: int) -> bool:
    return await session.get(StoredToken, github_id) is not None


async def forget_private_data(session: AsyncSession, github_id: int) -> None:
    """Delete a user's stored tokens and the private repos they shared."""
    await session.execute(delete(StoredToken).where(StoredToken.github_id == github_id))
    await delete_private_repos(session, github_id)
    await session.commit()


async def usable_access_token(
    session: AsyncSession,
    cipher: TokenCipher,
    oauth: GitHubOAuth | None,
    github_id: int,
    *,
    now: dt.datetime,
) -> str:
    """A working access token for the user, renewed first if it has (nearly) expired.

    Raises PrivateAccessError when there is none: never stored, the refresh token expired too,
    the user revoked the app, or the encryption key changed. Signing in again fixes all of these.
    """
    stored = await session.get(StoredToken, github_id)
    if stored is None:
        raise PrivateAccessError(SIGN_IN_AGAIN)
    try:
        if stored.access_expires_at is None or stored.access_expires_at - RENEW_BEFORE > now:
            return cipher.decrypt(stored.access_token)
        refresh_expired = stored.refresh_expires_at is not None and stored.refresh_expires_at <= now
        if stored.refresh_token is None or refresh_expired or oauth is None:
            raise PrivateAccessError(SIGN_IN_AGAIN)
        # Refresh tokens work once, so the new pair must be stored right away.
        renewed = await oauth.refresh(cipher.decrypt(stored.refresh_token))
    except (TokenDecryptError, OAuthError) as exc:
        logger.warning("No usable GitHub token for user %s: %s", github_id, exc)
        raise PrivateAccessError(SIGN_IN_AGAIN) from exc
    await keep_token(session, cipher, github_id, renewed)
    return renewed.access_token
