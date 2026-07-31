from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from grocea.constants import LOCAL_USER_ID
from grocea.db import get_session
from grocea.errors import DomainError
from grocea.models import User

DbSession = Annotated[Session, Depends(get_session)]


def get_current_user(session: DbSession) -> User:
    user = session.get(User, LOCAL_USER_ID)
    if user is None:
        raise DomainError(503, "LOCAL_PROFILE_NOT_SEEDED", "Local Profile has not been seeded.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class MutationHeaders:
    def __init__(
        self,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        device_id: Annotated[str, Header(alias="X-Device-ID")],
    ) -> None:
        from uuid import UUID

        self.mutation_id = UUID(idempotency_key)
        self.device_id = UUID(device_id)


MutationRequest = Annotated[MutationHeaders, Depends()]
