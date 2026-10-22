"""Saved-work routes (query_api.workspaces): workspaces, their items and "Continue where you
left off". The user is only ever the JWT `sub`; no route takes a user id. Another user's
workspace or item answers exactly like one that doesn't exist (404, same body), and a saved
item's state is validated against its kind's schema and size cap before it is stored.

These routes read and write only the caller's own saved settings, never data: opening an item
means the web app reads the data through the permission-checked routes as usual."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from query_api.store import Store
from query_api.workspaces import (
    ITEM_NAME,
    MAX_BODY_BYTES,
    WORKSPACE_NAME,
    Kind,
    LimitReached,
    StateError,
    UnknownUser,
    Workspaces,
    clean_name,
    validate_state,
)

PREFIXES = ("/v1/workspaces", "/v1/workspace-items", "/v1/recent")
NOT_FOUND = "workspace not found"
ITEM_NOT_FOUND = "item not found"


def _name(limit: int) -> Callable[[str], str]:
    return lambda v: clean_name(v, limit)


WorkspaceName = Annotated[str, Field(max_length=4 * WORKSPACE_NAME),
                          AfterValidator(_name(WORKSPACE_NAME))]
ItemName = Annotated[str, Field(max_length=4 * ITEM_NAME), AfterValidator(_name(ITEM_NAME))]
WorkspaceId = Annotated[UUID, Path(description="a workspace_id")]
ItemId = Annotated[UUID, Path(description="an item_id")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewWorkspace(_Strict):
    name: WorkspaceName


class CopyWorkspace(_Strict):
    name: WorkspaceName | None = None


class NewItem(_Strict):
    kind: Kind
    name: ItemName
    state: dict[str, Any]


class ItemPatch(_Strict):
    name: ItemName | None = None
    state: dict[str, Any] | None = None


class CopyItem(_Strict):
    workspace_id: UUID | None = Field(default=None, description="default: the same workspace")
    name: ItemName | None = None


class FilingRead(_Strict):
    company_id: str = Field(pattern=r"^\d{10}$")
    accession_no: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    section_key: str | None = Field(default=None, pattern=r"^[A-Z0-9.?-]{1,8}$")


def add_workspace_routes(app: FastAPI, *, store: Store, workspaces: Workspaces | None,
                         current_user: Callable[..., str]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.middleware("http")
    async def cap_body(request: Request, call_next):
        """Bodies of the saved-work routes are small JSON: refuse a large or unsized one before
        reading it."""
        if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith(PREFIXES):
            size = request.headers.get("content-length")
            if size is None or not size.isdigit():
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "send a Content-Length"}, status_code=411)
            elif int(size) > MAX_BODY_BYTES:
                return JSONResponse({"detail": f"body larger than {MAX_BODY_BYTES} bytes"},
                                    status_code=413)
        return await call_next(request)

    def ws() -> Workspaces:
        if workspaces is None:
            raise HTTPException(503, "workspaces unavailable")
        return workspaces

    def state_of(kind: str, raw: Any) -> dict:
        try:
            return validate_state(kind, raw)
        except StateError as e:
            raise HTTPException(422, f"invalid {kind} state: {e}") from None

    def write(f: Callable[[], Any]) -> Any:
        try:
            return f()
        except LimitReached as e:
            raise HTTPException(409, str(e)) from None
        except UnknownUser:
            raise HTTPException(409, "no account to save work to") from None

    def found(row: Any, what: str = NOT_FOUND) -> Any:
        if row is None or row is False:
            raise HTTPException(404, what)
        return row

    @app.get("/v1/workspaces", response_model=dict)
    def list_workspaces(user_id: User) -> dict:
        """The caller's workspaces, oldest first, each with its items (no states)."""
        return {"workspaces": ws().list(user_id)}

    @app.post("/v1/workspaces", status_code=201, response_model=dict)
    def create_workspace(user_id: User, body: NewWorkspace) -> dict:
        return write(lambda: ws().create(user_id, body.name))

    @app.patch("/v1/workspaces/{workspace_id}", response_model=dict)
    def rename_workspace(user_id: User, workspace_id: WorkspaceId, body: NewWorkspace) -> dict:
        return found(ws().rename(user_id, str(workspace_id), body.name))

    @app.delete("/v1/workspaces/{workspace_id}", response_model=dict)
    def delete_workspace(user_id: User, workspace_id: WorkspaceId) -> dict:
        """Deletes the workspace and every item in it."""
        found(ws().delete(user_id, str(workspace_id)))
        return {"workspace_id": str(workspace_id), "deleted": True}

    @app.post("/v1/workspaces/{workspace_id}/duplicate", status_code=201, response_model=dict)
    def duplicate_workspace(user_id: User, workspace_id: WorkspaceId,
                            body: CopyWorkspace | None = None) -> dict:
        name = body.name if body else None
        return found(write(lambda: ws().duplicate(user_id, str(workspace_id), name)))

    @app.post("/v1/workspaces/{workspace_id}/items", status_code=201, response_model=dict)
    def add_item(user_id: User, workspace_id: WorkspaceId, body: NewItem) -> dict:
        """Saves a chart, comps set or screen. The state is the web app's own (chart builder
        layers and options, comps target/peers/columns, screener filters), validated here."""
        state = state_of(body.kind, body.state)
        return found(write(lambda: ws().add_item(user_id, str(workspace_id), body.kind,
                                                 body.name, state)))

    @app.get("/v1/workspace-items/{item_id}", response_model=dict)
    def get_item(user_id: User, item_id: ItemId) -> dict:
        return found(ws().item(user_id, str(item_id)), ITEM_NOT_FOUND)

    @app.post("/v1/workspace-items/{item_id}/open", response_model=dict)
    def open_item(user_id: User, item_id: ItemId) -> dict:
        """The item with its state, recorded as opened now (Home's "Continue where you left
        off")."""
        found(ws().mark_opened(user_id, str(item_id)), ITEM_NOT_FOUND)
        return found(ws().item(user_id, str(item_id)), ITEM_NOT_FOUND)

    @app.put("/v1/workspace-items/{item_id}", response_model=dict)
    def update_item(user_id: User, item_id: ItemId, body: ItemPatch) -> dict:
        """Save over an item (its state, validated against its kind) and/or rename it."""
        if body.name is None and body.state is None:
            raise HTTPException(422, "nothing to change: give name, state or both")
        current = found(ws().item(user_id, str(item_id)), ITEM_NOT_FOUND)
        state = state_of(current["kind"], body.state) if body.state is not None else None
        return found(ws().update_item(user_id, str(item_id), body.name, state), ITEM_NOT_FOUND)

    @app.post("/v1/workspace-items/{item_id}/duplicate", status_code=201, response_model=dict)
    def duplicate_item(user_id: User, item_id: ItemId, body: CopyItem | None = None) -> dict:
        """A copy in the same workspace, or in another of the caller's workspaces."""
        target = str(body.workspace_id) if body and body.workspace_id else None
        name = body.name if body else None
        return found(write(lambda: ws().duplicate_item(user_id, str(item_id), target, name)),
                     ITEM_NOT_FOUND)

    @app.delete("/v1/workspace-items/{item_id}", response_model=dict)
    def delete_item(user_id: User, item_id: ItemId) -> dict:
        found(ws().delete_item(user_id, str(item_id)), ITEM_NOT_FOUND)
        return {"item_id": str(item_id), "deleted": True}

    @app.get("/v1/recent", response_model=dict)
    def recent(user_id: User, limit: Annotated[int, Query(ge=1, le=20)] = 8) -> dict:
        """Recently opened saved items and recently read filings, newest first. Filings carry
        public index metadata only (company name, form, dates)."""
        items = ws().recent(user_id, limit)
        reads = ws().recent_filings(user_id, limit)
        meta = store.filings_meta(user_id, [r["accession_no"] for r in reads]) if reads else {}
        names = store.profiles(user_id, sorted({r["company_id"] for r in reads})) if reads else {}
        return {"items": items, "filings": [
            {**r, "name": (names.get(r["company_id"]) or {}).get("name"),
             **{k: (meta.get(r["accession_no"]) or {}).get(k)
                for k in ("form", "filed", "period_end")}}
            for r in reads]}

    @app.put("/v1/recent/filings", response_model=dict)
    def read_filing(user_id: User, body: FilingRead) -> dict:
        """Remember that the caller opened this filing (the newest 20 are kept). Grants
        nothing: the reader still checks access when it is opened again."""
        write(lambda: ws().read_filing(user_id, body.company_id, body.accession_no,
                                       body.section_key))
        return {"accession_no": body.accession_no, "recorded": True}
