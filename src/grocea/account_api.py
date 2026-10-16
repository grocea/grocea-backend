from fastapi import APIRouter, Response

from grocea.account import delete_account, export_account
from grocea.auth_api import _clear_session_cookie, _no_store
from grocea.dependencies import CurrentAuth, DbSession
from grocea.schemas import AccountDeleteRequest, AccountExportResponse

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/export", response_model=AccountExportResponse)
def read_export(auth: CurrentAuth, session: DbSession, response: Response) -> AccountExportResponse:
    _no_store(response)
    response.headers["Content-Disposition"] = 'attachment; filename="grocea-account.json"'
    return export_account(session, auth.user)


@router.delete("", status_code=204)
def remove_account(
    payload: AccountDeleteRequest, auth: CurrentAuth, session: DbSession, response: Response
) -> Response:
    delete_account(session, auth.user, payload.current_password)
    _clear_session_cookie(response)
    _no_store(response)
    response.status_code = 204
    return response
