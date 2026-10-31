"""The endpoints for models on proxies, and the proxy import, on the registry router.

A write changes the registry tables, and it reads a proxy only to import it the first time. A dry run reads
the proxies that it touches. The sync module pushes a write to a registry-managed proxy.
"""

from collections import defaultdict
from datetime import date

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.dry_run import deployment_plans, short_reason
from app.registry.api.sync import _save
from app.registry.api.routes import (
    _deployment_responses,
    _find_model,
    _find_proxy,
    _lock_proxies,
    _model_query,
    _split_key,
    Refused,
    refuse,
    router,
)
from app.registry.importer import _import, import_proxy
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.config import registry_manages
from app.registry.planner import CANNOT_CLEAR_CREDENTIAL, DEPLOYMENT_FIELDS
from app.registry.rules import (
    backend_problem,
    credential_provider,
    default_backend,
    enable_problems,
    geo_problem,
    removal_problem,
    support_rows,
)
from app.registry.schemas import (
    RegistryDryRun,
    RegistryModelDeploymentResponse,
    RegistryProblem,
    RegistryProxyModelUpdate,
    RegistryRollout,
    RegistryRolloutEntry,
    RegistrySyncStarted,
)
from app.registry.support import Availability


def _import_problems(proxies, names: dict[int, str]) -> list[RegistryProblem]:
    """A write on a proxy that the importer has not read is refused, before the other checks.

    The import would add the live deployments next to the rows this API wrote, so one model would have two rows.
    The other checks would also read the deployments as they are before the import.
    """
    return [
        RegistryProblem(message=f"Import the proxy {name} first", proxy=name, status=409)
        for name in sorted(names[proxy.region_id] for proxy in proxies if proxy.imported_at is None)
    ]


def _import_failed(proxy: str, exc: Exception) -> RegistryProblem:
    return RegistryProblem(
        message=f"The automatic import of {proxy} failed: {short_reason(exc)}", proxy=proxy, status=409
    )


def _unknown_names(db: Session, slugs, credentials: dict[str, str]) -> list[RegistryProblem]:
    """Unknown access groups and credential names, per proxy name for a credential. They need no proxy read, so
    they run before the automatic import, and a write that they refuse changes nothing."""
    found_groups = {slug for (slug,) in db.query(DBRegistryAccessGroup.slug).filter(DBRegistryAccessGroup.slug.in_(slugs))}
    names = set(credentials.values())
    found_credentials = {
        name for (name,) in db.query(DBRegistryCredential.name).filter(DBRegistryCredential.name.in_(names))
    }
    return [
        *(RegistryProblem(message=f"Unknown access group {slug}") for slug in sorted(set(slugs) - found_groups)),
        *(
            RegistryProblem(message=f"Unknown credential {name}", proxy=proxy)
            for proxy, name in credentials.items()
            if name not in found_credentials
        ),
    ]


def _import_first(db: Session, regions, early: list[RegistryProblem]) -> None:
    """Adopt-import each proxy that the adopt import never read, before the write locks it.

    Each import commits the request session, so the `early` problems, which need no import, refuse the write
    first. With no import to run, they join the full refusal later. The import reads the proxy, so it runs
    before any lock.
    """
    regions = {region.id: region for region in regions}
    waiting = [
        region_id
        for (region_id,) in db.query(DBRegistryProxy.region_id)
        .filter(DBRegistryProxy.region_id.in_(regions), DBRegistryProxy.adopted_at.is_(None))
        .order_by(DBRegistryProxy.region_id)
    ]
    if waiting:
        refuse(early)
    for region_id in waiting:
        region = regions[region_id]
        name = region.name
        try:
            import_proxy(db, region, adopt=True)
        except Exception as exc:
            raise Refused([_import_failed(name, exc)]) from None


def _group_ids(db: Session, slugs: list[str], problems: list[RegistryProblem]) -> dict[str, int]:
    """The ids of the groups, locked until the links are written.

    FOR KEY SHARE waits for a group PUT or DELETE that holds the group, so the link write cannot deadlock with
    it or fail on a deleted group. A group deleted in the meantime is then unknown. The sorted order keeps
    the lock order the same in every call.
    """
    found = dict(
        db.query(DBRegistryAccessGroup.slug, DBRegistryAccessGroup.id)
        .filter(DBRegistryAccessGroup.slug.in_(slugs))
        .order_by(DBRegistryAccessGroup.slug)
        .with_for_update(read=True, key_share=True)
    )
    problems += [RegistryProblem(message=f"Unknown access group {slug}") for slug in sorted(set(slugs) - found.keys())]
    return found


def _credential_problems(db: Session, wanted: list[tuple[str, int, str, str, str | None]]) -> list[RegistryProblem]:
    """Unknown credentials, credentials not on the proxy, and rule 6, for each
    (credential name, region id, proxy name, model key, model provider), in a fixed number of queries.
    A null model provider skips rule 6.

    FOR KEY SHARE makes a credential DELETE wait for this write, or the write sees the credential as unknown.
    The sorted order keeps the lock order the same in every call.
    """
    names = sorted({name for name, *_ in wanted})
    if not names:
        return []
    rows = {
        row.name: row
        for row in db.query(DBRegistryCredential)
        .filter(DBRegistryCredential.name.in_(names))
        .order_by(DBRegistryCredential.name)
        .with_for_update(read=True, key_share=True)
    }
    links = set(
        db.query(DBRegistryCredentialRegion.credential_id, DBRegistryCredentialRegion.region_id).filter(
            DBRegistryCredentialRegion.credential_id.in_([row.id for row in rows.values()])
        )
    )
    # A credential from the adopt import has no provider, so it gets the providers of its users, on any proxy.
    providers = {name: {credential_provider(row.provider)} for name, row in rows.items() if row.provider}
    unset = [name for name, row in rows.items() if not row.provider]
    if unset:
        used_by = DBRegistryModelRegion.litellm_params["litellm_credential_name"].as_string()
        for name, provider in (
            db.query(used_by, DBRegistryProvider.name)
            .join(DBRegistryModel, DBRegistryModel.id == DBRegistryModelRegion.model_id)
            .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
            .filter(used_by.in_(unset))
            .distinct()
        ):
            providers.setdefault(name, set()).add(credential_provider(provider))

    problems = []
    for name, region_id, proxy, key, provider in wanted:
        row = rows.get(name)
        if row is None:
            problems.append(RegistryProblem(message=f"Unknown credential {name}", proxy=proxy))
            continue
        if (row.id, region_id) not in links:
            problems.append(RegistryProblem(
                message=f"The credential {name} is not on the proxy {proxy}", proxy=proxy, status=409
            ))
        allowed = providers.get(name)
        if provider and allowed and credential_provider(provider) not in allowed:
            problems.append(RegistryProblem(
                message=f"the credential {name} is for {', '.join(sorted(allowed))}, not {provider}",
                proxy=proxy, model=key,
            ))
    return problems


def _alias_names(db: Session, region_ids) -> set[tuple[int, str]]:
    return set(
        db.query(DBRegistryAlias.region_id, DBRegistryAlias.alias).filter(DBRegistryAlias.region_id.in_(region_ids))
    )


def _alias_name_problem(name: str) -> str:
    # LiteLLM would send the calls for the alias to this deployment, or the other way round.
    return f"the model name {name} is an alias on this proxy"


def _name_owners(db: Session, region_ids, names) -> dict[tuple[int, str], set[int]]:
    """The ids of the models that have a deployment with each of the names, per proxy."""
    owners = defaultdict(set)
    for region_id, name, model_id in db.query(
        DBRegistryModelRegion.region_id, DBRegistryModelRegion.model_name, DBRegistryModelRegion.model_id
    ).filter(DBRegistryModelRegion.region_id.in_(region_ids), DBRegistryModelRegion.model_name.in_(names)):
        owners[(region_id, name)].add(model_id)
    return owners


def _used_name_problem(owners, region_id: int, name: str, model_id: int) -> str | None:
    # LiteLLM would send the calls for the name to either model. Deployments of one model may share a name.
    if owners.get((region_id, name), set()) - {model_id}:
        return f"the model name {name} is used by another model on this proxy"
    return None


def _alias_problem(db: Session, region_id: int, names, changed_ids: list[int]) -> str | None:
    """Why a delete or a rename leaves an alias on the proxy with no target, or None. `force` never skips it."""
    names = set(names)
    kept = {
        name
        for (name,) in db.query(DBRegistryModelRegion.model_name).filter(
            DBRegistryModelRegion.region_id == region_id,
            DBRegistryModelRegion.model_name.in_(names),
            DBRegistryModelRegion.id.not_in(changed_ids),
        )
    }
    # A name that another deployment keeps still works as a target.
    left = names - kept
    if not left:
        return None
    aliases = [
        alias
        for (alias,) in db.query(DBRegistryAlias.alias)
        .filter(DBRegistryAlias.region_id == region_id, DBRegistryAlias.target.in_(left))
        .order_by(DBRegistryAlias.alias)
    ]
    if aliases:
        return f"Aliases on this proxy point to this model name: {', '.join(aliases)}. Move or remove them first"
    return None


def _deployments(db: Session, model: DBRegistryModel, region_id: int) -> list[DBRegistryModelRegion]:
    return (
        db.query(DBRegistryModelRegion)
        .filter_by(model_id=model.id, region_id=region_id)
        .order_by(DBRegistryModelRegion.model_name, DBRegistryModelRegion.id)
        .all()
    )


def _set_credential(deployment: DBRegistryModelRegion, name: str | None) -> None:
    # A new dict, because the JSON column does not see a change in place.
    params = {k: v for k, v in (deployment.litellm_params or {}).items() if k != "litellm_credential_name"}
    if name is not None:
        params["litellm_credential_name"] = name
    deployment.litellm_params = params


def _add_deployment(
    db: Session, model: DBRegistryModel, region_id: int, model_name: str, backend: str, credential: str | None,
    enabled: bool,
) -> DBRegistryModelRegion:
    # No LiteLLM deployment id and no price yet: the proxy has not seen this deployment.
    deployment = DBRegistryModelRegion(
        model_id=model.id, region_id=region_id, model_name=model_name, litellm_model=backend,
        prices={}, model_info={}, enabled=enabled,
    )
    _set_credential(deployment, credential)
    db.add(deployment)
    return deployment


@router.put(
    "/proxies/{proxy}/models/{key:path}",
    response_model=list[RegistryModelDeploymentResponse]
    | RegistryDryRun[list[RegistryModelDeploymentResponse]]
    | RegistrySyncStarted[list[RegistryModelDeploymentResponse]],
    responses={
        201: {"model": list[RegistryModelDeploymentResponse], "description": "The deployment is created"},
        202: {"model": RegistrySyncStarted[list[RegistryModelDeploymentResponse]], "description": "A sync run started"},
    },
)
def set_proxy_model(
    proxy: str,
    key: str,
    response: Response,
    background: BackgroundTasks,
    body: RegistryProxyModelUpdate | None = None,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    region = _find_proxy(db, proxy)
    model, provider = _find_model(db, key)
    changes = (body or RegistryProxyModelUpdate()).model_dump(exclude_unset=True)
    if not dry_run:
        credential = {region.name: changes["credential"]} if changes.get("credential") else {}
        _import_first(db, [region], _unknown_names(db, changes.get("groups") or [], credential))
    proxy_row = _lock_proxies(db, [region.id])[region.id]
    refuse(_import_problems([proxy_row], {region.id: region.name}))
    cloud_regions = proxy_row.cloud_regions or {}
    problems: list[RegistryProblem] = []
    group_ids = _group_ids(db, changes["groups"], problems) if "groups" in changes else None
    deployments = _deployments(db, model, region.id)

    def add(message: str, status: int = 422) -> None:
        problems.append(RegistryProblem(message=message, proxy=region.name, model=key, status=status))

    prices = db.query(DBRegistryModelPrice).filter_by(model_id=model.id).all()

    def check_enable(availability: Availability, targets: list[tuple[str, dict]]) -> None:
        """Rule 4 for each (backend, prices stored on the deployment) that will be on."""
        if force:
            return
        support = support_rows(db, [model.id], [region.id]).get((model.id, region.id))
        for backend, own_prices in targets:
            for problem in enable_problems(
                model, provider, support, availability, cloud_regions.get(provider), backend, prices, own_prices
            ):
                add(problem)

    # Every check runs before the first change, so a 4xx changes nothing.
    if not deployments:
        availability = Availability(db)
        name = changes.get("model_name", model.model_id)
        backend = changes.get("litellm_model") or default_backend(provider, model.model_id, cloud_regions, availability)
        if (region.id, name) in _alias_names(db, [region.id]):
            add(_alias_name_problem(name))
        if problem := _used_name_problem(_name_owners(db, [region.id], [name]), region.id, name, model.id):
            add(problem)
        if problem := backend_problem(backend, provider, model.model_id, cloud_regions):
            add(problem)
        enabled = changes.get("enabled", True)
        if enabled:
            check_enable(availability, [(backend, {})])
        if changes.get("credential"):
            problems += _credential_problems(db, [(changes["credential"], region.id, region.name, key, provider)])
        refuse(problems)
        deployments = [_add_deployment(db, model, region.id, name, backend, changes.get("credential"), enabled)]
        db.flush()
        response.status_code = 201
    else:
        several = len(deployments) > 1 and bool(changes.keys() & {"model_name", "litellm_model"})
        if several:
            add(
                "The model has several deployments on this proxy, so the change cannot pick one model_name "
                "or litellm_model"
            )
        if "litellm_model" in changes and (
            problem := backend_problem(changes["litellm_model"], provider, model.model_id, cloud_regions)
        ):
            add(problem)
        old_name = deployments[0].model_name
        # With several deployments, the rename is already refused, and the first one is not the one renamed.
        if not several and changes.get("model_name", old_name) != old_name:
            if (region.id, changes["model_name"]) in _alias_names(db, [region.id]):
                add(_alias_name_problem(changes["model_name"]))
            owners = _name_owners(db, [region.id], [changes["model_name"]])
            if problem := _used_name_problem(owners, region.id, changes["model_name"], model.id):
                add(problem)
            if problem := _alias_problem(db, region.id, [old_name], [deployments[0].id]):
                add(problem, 409)
        enabled = changes.get("enabled")
        if enabled is False and not force and any(d.enabled for d in deployments):
            if problem := removal_problem(model, date.today()):
                add(problem, 409)
        # A new backend string on a deployment that is on after the PUT can be one the region cannot run.
        # A change of only groups, credential or model_name keeps the backend, so it skips rules 4 and 5.
        checked = [
            (changes.get("litellm_model", d.litellm_model), d.prices)
            for d in deployments
            if changes.get("enabled", d.enabled)
            and (not d.enabled or changes.get("litellm_model", d.litellm_model) != d.litellm_model)
        ]
        # A stored backend gets the region part of rule 5; a new one had the full check above. Force skips neither.
        if "litellm_model" not in changes:
            for backend, _ in checked:
                if problem := geo_problem(backend, provider, cloud_regions):
                    add(problem)
        if checked:
            check_enable(Availability(db), checked)
        # The sync never creates a live deployment again, and a PATCH keeps the old credential name.
        if "credential" in changes and changes["credential"] is None and registry_manages(region.name) and any(
            d.litellm_deployment_id and (d.litellm_params or {}).get("litellm_credential_name") for d in deployments
        ):
            add(CANNOT_CLEAR_CREDENTIAL, 409)
        if changes.get("credential"):
            problems += _credential_problems(db, [(changes["credential"], region.id, region.name, key, provider)])
        refuse(problems)

        for deployment in deployments:
            for field in ("model_name", "litellm_model", "enabled"):
                if field in changes:
                    setattr(deployment, field, changes[field])
            if "credential" in changes:
                _set_credential(deployment, changes["credential"])
        if group_ids is not None:
            db.query(DBRegistryModelRegionGroup).filter(
                DBRegistryModelRegionGroup.model_region_id.in_([d.id for d in deployments])
            ).delete(synchronize_session=False)

    if group_ids:
        db.add_all(
            DBRegistryModelRegionGroup(model_region_id=d.id, access_group_id=group_id)
            for d in deployments
            for group_id in group_ids.values()
        )
    db.flush()
    # The model's deployments on the proxy, in the shape of the model detail.
    result = _deployment_responses(
        db, prices, provider, [(d, region.name) for d in _deployments(db, model, region.id)]
    )
    return _save(
        db, dry_run, result, response=response, background=background,
        plan=lambda: deployment_plans(db, deployments, DEPLOYMENT_FIELDS),
    )


@router.delete(
    "/proxies/{proxy}/models/{key:path}",
    response_model=RegistryDryRun[None] | RegistrySyncStarted[None],
    responses={
        204: {"description": "The model's deployments on the proxy are deleted"},
        202: {"model": RegistrySyncStarted[None], "description": "A sync run started"},
    },
)
def remove_proxy_model(
    proxy: str,
    key: str,
    response: Response,
    background: BackgroundTasks,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    region = _find_proxy(db, proxy)
    model, _ = _find_model(db, key)
    if not dry_run:
        _import_first(db, [region], [])
    refuse(_import_problems(_lock_proxies(db, [region.id]).values(), {region.id: region.name}))
    deployments = _deployments(db, model, region.id)
    if not deployments:
        raise HTTPException(status_code=404, detail="The model has no deployment on this proxy")
    ids = [d.id for d in deployments]
    found = []
    if problem := _alias_problem(db, region.id, [d.model_name for d in deployments], ids):
        found.append(problem)
    if not force and (problem := removal_problem(model, date.today())):
        found.append(problem)
    refuse([RegistryProblem(message=problem, proxy=region.name, model=key, status=409) for problem in found])
    # The foreign key of the group links cascades, so this also removes them.
    db.query(DBRegistryModelRegion).filter(DBRegistryModelRegion.id.in_(ids)).delete(synchronize_session=False)
    # The rows were loaded before the delete, so the plan still reads their attributes.
    return _save(
        db, dry_run, Response(status_code=204), response=response, background=background,
        plan=lambda: deployment_plans(db, deployments, DEPLOYMENT_FIELDS, removed=True),
    )


@router.post(
    "/rollouts",
    response_model=list[RegistryRolloutEntry]
    | RegistryDryRun[list[RegistryRolloutEntry]]
    | RegistrySyncStarted[list[RegistryRolloutEntry]],
    responses={202: {"model": RegistrySyncStarted[list[RegistryRolloutEntry]], "description": "A sync run started"}},
)
def roll_out(
    body: RegistryRollout,
    response: Response,
    background: BackgroundTasks,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Put each model on each proxy, all or nothing. The query count does not grow with the pairs."""
    regions = {
        region.name: region
        for region in db.query(DBRegion)
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .filter(DBRegion.name.in_(body.proxies))
    }
    problems = [
        RegistryProblem(message=f"Unknown proxy {name}", proxy=name) for name in body.proxies if name not in regions
    ]
    region_ids = [region.id for region in regions.values()]
    keys = list(dict.fromkeys(body.models))
    idents = {_split_key(key) for key in keys}
    models = {
        f"{provider}/{model.model_id}": (model, provider)
        for model, provider in _model_query(db).filter(
            tuple_(DBRegistryProvider.name, DBRegistryModel.model_id).in_(idents)
        )
    }
    unknown_models = [RegistryProblem(message=f"Unknown model {key}", model=key) for key in keys if key not in models]
    if not dry_run:
        credentials = {name: credential for name, credential in body.proxies.items() if credential}
        _import_first(db, regions.values(), [*problems, *unknown_models, *_unknown_names(db, body.groups, credentials)])
    proxies = _lock_proxies(db, region_ids)
    # One proxy that is not imported refuses the whole call: the admin imports it, then runs the rollout again.
    problems += _import_problems(proxies.values(), {region.id: region.name for region in regions.values()})
    problems += unknown_models
    # The rules below need a known model on a known, imported proxy for each pair.
    refuse(problems)
    group_ids = _group_ids(db, body.groups, problems)

    model_ids = [model.id for model, _ in models.values()]
    deployments: dict[tuple[int, int], list[DBRegistryModelRegion]] = defaultdict(list)
    for row in (
        db.query(DBRegistryModelRegion)
        .filter(DBRegistryModelRegion.model_id.in_(model_ids), DBRegistryModelRegion.region_id.in_(region_ids))
        .order_by(DBRegistryModelRegion.model_name, DBRegistryModelRegion.id)
    ):
        deployments[(row.model_id, row.region_id)].append(row)
    links = set(
        db.query(DBRegistryModelRegionGroup.model_region_id, DBRegistryModelRegionGroup.access_group_id).filter(
            DBRegistryModelRegionGroup.model_region_id.in_([d.id for rows in deployments.values() for d in rows])
        )
    )
    supports = support_rows(db, model_ids, region_ids)
    prices: dict[int, list[DBRegistryModelPrice]] = defaultdict(list)
    for price in db.query(DBRegistryModelPrice).filter(DBRegistryModelPrice.model_id.in_(model_ids)):
        prices[price.model_id].append(price)
    availability = Availability(db)
    aliases = _alias_names(db, region_ids)
    owners = _name_owners(db, region_ids, [model.model_id for model, _ in models.values()])

    # Every check runs before the first write, so a 4xx changes nothing.
    entries, pairs, credentials = [], [], []
    for key in keys:
        model, provider = models[key]
        for name in body.proxies:
            region_id = regions[name].id
            cloud_regions = proxies[region_id].cloud_regions or {}
            support = supports.get((model.id, region_id))
            existing = deployments.get((model.id, region_id))
            found = []
            if existing:
                action, model_name, backend = "updated", existing[0].model_name, existing[0].litellm_model
                for deployment in existing:
                    if deployment.enabled:
                        continue
                    # The region part of rule 5 covers a stored backend too, and force never skips it.
                    if problem := geo_problem(deployment.litellm_model, provider, cloud_regions):
                        found.append(problem)
                    if not force:
                        found += enable_problems(
                            model, provider, support, availability, cloud_regions.get(provider),
                            deployment.litellm_model, prices[model.id], deployment.prices,
                        )
            else:
                action, model_name = "created", model.model_id
                backend = default_backend(provider, model.model_id, cloud_regions, availability)
                if (region_id, model_name) in aliases:
                    found.append(_alias_name_problem(model_name))
                if problem := _used_name_problem(owners, region_id, model_name, model.id):
                    found.append(problem)
                # Two models of this rollout with the same model id must not share the name either.
                owners[(region_id, model_name)].add(model.id)
                if problem := backend_problem(backend, provider, model.model_id, cloud_regions):
                    found.append(problem)
                if not force:
                    found += enable_problems(
                        model, provider, support, availability, cloud_regions.get(provider), backend, prices[model.id]
                    )
            problems += [RegistryProblem(message=problem, proxy=name, model=key) for problem in found]
            entries.append(RegistryRolloutEntry(
                proxy=name, model=key, action=action, model_name=model_name, litellm_model=backend,
            ))
            pairs.append((model, region_id, existing))
            if body.proxies[name]:
                # Rule 6 only where the rollout writes the credential; a deployment that exists keeps its own.
                credentials.append((body.proxies[name], region_id, name, key, None if existing else provider))
    problems += _credential_problems(db, credentials)
    refuse(problems)

    targets = []
    for entry, (model, region_id, existing) in zip(entries, pairs, strict=True):
        if existing:
            # The credential of a deployment that exists stays as it is.
            for deployment in existing:
                deployment.enabled = True
        else:
            existing = [_add_deployment(
                db, model, region_id, entry.model_name, entry.litellm_model, body.proxies[entry.proxy], True,
            )]
        targets += existing
    db.flush()
    # Add the groups and keep the links the deployments already have.
    db.add_all(
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=group_id)
        for deployment in targets
        for group_id in group_ids.values()
        if (deployment.id, group_id) not in links
    )
    return _save(
        db, dry_run, entries, response=response, background=background,
        plan=lambda: deployment_plans(db, targets, DEPLOYMENT_FIELDS),
    )


@router.post("/proxies/{proxy}/import", response_model=dict | RegistryDryRun[dict])
def import_proxy_now(proxy: str, dry_run: bool = Query(False), db: Session = Depends(get_db)):
    """Adopt-import the proxy: add what the registry does not have and keep every row."""
    region = _find_proxy(db, proxy)
    name = region.name
    try:
        if dry_run:
            stats = _import(db, region, adopt=True)
            db.rollback()
            return RegistryDryRun(result=stats, steps=[])
        return import_proxy(db, region, adopt=True)
    except Exception as exc:
        db.rollback()
        raise Refused([_import_failed(name, exc)]) from None
