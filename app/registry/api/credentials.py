"""The credential endpoints, on the registry router. No response holds a credential value."""

from collections import defaultdict

from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.sync import _save
from app.registry.api.routes import Refused, refuse, router
from app.registry.crypto import MissingKey, decrypt_values, encrypt_values
from app.registry.litellm import without_secrets
from app.registry.models import (
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModelRegion,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.schemas import (
    RegistryCredentialCreate,
    RegistryCredentialResponse,
    RegistryCredentialUpdate,
    RegistryDryRun,
    RegistryProblem,
)


def _credential_responses(db: Session, ids: list[int] | None = None) -> list[RegistryCredentialResponse]:
    credentials = db.query(DBRegistryCredential).order_by(DBRegistryCredential.name)
    links = (
        db.query(DBRegistryCredentialRegion.credential_id, DBRegion.name)
        .join(DBRegion, DBRegion.id == DBRegistryCredentialRegion.region_id)
        .order_by(DBRegion.name)
    )
    if ids is not None:
        credentials = credentials.filter(DBRegistryCredential.id.in_(ids))
        links = links.filter(DBRegistryCredentialRegion.credential_id.in_(ids))
    proxies = defaultdict(list)
    for credential_id, name in links:
        proxies[credential_id].append(name)
    return [
        RegistryCredentialResponse(
            name=row.name, provider=row.provider, info=row.credential_info or {}, proxies=proxies[row.id]
        )
        for row in credentials
    ]


def _find_credential(db: Session, name: str) -> DBRegistryCredential:
    row = db.query(DBRegistryCredential).filter_by(name=name).with_for_update().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return row


def _proxy_ids(db: Session, names: list[str], problems: list[RegistryProblem]) -> dict[str, int]:
    """The region id of each proxy name; a name that is not a registry proxy adds a 422."""
    found = dict(
        db.query(DBRegion.name, DBRegion.id)
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .filter(DBRegion.name.in_(set(names)))
    )
    problems += [
        RegistryProblem(message=f"{name} is not a proxy of the registry", proxy=name)
        for name in sorted(set(names) - set(found))
    ]
    return found


def _encrypted(values: dict, problems: list[RegistryProblem]) -> str | None:
    try:
        return encrypt_values(values)
    except MissingKey as exc:
        problems.append(RegistryProblem(message=str(exc), status=409))
        return None


def _uses(db: Session, name: str):
    """The deployments that name the credential."""
    return db.query(DBRegistryModelRegion).filter(
        DBRegistryModelRegion.litellm_params["litellm_credential_name"].as_string() == name
    )


@router.get("/credentials", response_model=list[RegistryCredentialResponse])
def list_credentials(response: Response, db: Session = Depends(get_db)):
    credentials = _credential_responses(db)
    response.headers["X-Total-Count"] = str(len(credentials))
    return credentials


@router.post(
    "/credentials",
    status_code=201,
    response_model=RegistryCredentialResponse | RegistryDryRun[RegistryCredentialResponse],
    responses={200: {"model": RegistryDryRun[RegistryCredentialResponse], "description": "The dry run"}},
)
def create_credential(
    body: RegistryCredentialCreate,
    response: Response,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    problems = []
    if not db.query(DBRegistryProvider.id).filter_by(name=body.provider).first():
        problems.append(RegistryProblem(message=f"Unknown provider {body.provider}"))
    if without_secrets(body.info) != body.info:
        problems.append(RegistryProblem(message="info holds a secret; put secrets in values"))
    region_ids = _proxy_ids(db, body.proxies, problems)
    token = _encrypted(body.values, problems)
    refuse(problems)

    row = DBRegistryCredential(
        name=body.name, provider=body.provider, values_encrypted=token, credential_info=body.info
    )
    db.add(row)
    # The unique name, not a lookup first, so two requests at the same time cannot both create it.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Refused([RegistryProblem(message="Credential already exists", status=409)]) from None
    db.add_all(DBRegistryCredentialRegion(credential_id=row.id, region_id=rid) for rid in region_ids.values())
    db.flush()
    return _save(db, dry_run, _credential_responses(db, [row.id])[0], response=response)


@router.patch(
    "/credentials/{name}",
    response_model=RegistryCredentialResponse | RegistryDryRun[RegistryCredentialResponse],
)
def update_credential(
    name: str,
    body: RegistryCredentialUpdate,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    # The lock makes a model write that names the credential wait, so a removed proxy stays unused.
    row = _find_credential(db, name)
    problems = []
    token = row.values_encrypted
    if body.values is not None:
        try:
            stored = decrypt_values(row.values_encrypted) if row.values_encrypted else {}
        except (MissingKey, InvalidToken):
            stored = None
            problems.append(RegistryProblem(
                message="The stored values cannot be decrypted with the keys in REGISTRY_CREDENTIAL_KEYS", status=409
            ))
        if stored is not None:
            token = _encrypted({**stored, **body.values}, problems)
    if body.proxies is not None:
        wanted = _proxy_ids(db, body.proxies, problems)
        current = {
            region_id
            for (region_id,) in db.query(DBRegistryCredentialRegion.region_id).filter_by(credential_id=row.id)
        }
        removed = current - set(wanted.values())
        in_use = {
            proxy
            for (proxy,) in _uses(db, name)
            .join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
            .filter(DBRegion.id.in_(removed))
            .with_entities(DBRegion.name)
        }
        problems += [
            RegistryProblem(message=f"A deployment on {proxy} uses the credential", proxy=proxy, status=409)
            for proxy in sorted(in_use)
        ]
    refuse(problems)

    row.values_encrypted = token
    if body.proxies is not None:
        db.query(DBRegistryCredentialRegion).filter_by(credential_id=row.id).delete()
        db.add_all(DBRegistryCredentialRegion(credential_id=row.id, region_id=rid) for rid in set(wanted.values()))
    db.flush()
    return _save(db, dry_run, _credential_responses(db, [row.id])[0])


@router.delete(
    "/credentials/{name}",
    response_model=RegistryDryRun[None],
    responses={204: {"description": "The credential is deleted"}},
)
def delete_credential(name: str, dry_run: bool = Query(False), db: Session = Depends(get_db)):
    row = _find_credential(db, name)
    if _uses(db, name).first():
        refuse([RegistryProblem(message="Deployments still use the credential", status=409)])
    db.delete(row)
    return _save(db, dry_run, Response(status_code=204))
