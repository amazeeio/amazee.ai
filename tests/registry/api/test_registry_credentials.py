import pytest
from cryptography.fernet import Fernet

from app.registry.config import CREDENTIAL_KEYS_ENV
from app.registry.crypto import decrypt_values
from app.registry.models import DBRegistryCredential, DBRegistryCredentialRegion, DBRegistryModelRegion
from tests.registry.api.conftest import auth, messages, ok

CREDENTIALS = "/registry/credentials"
SECRET = "AKIA-very-secret-value"


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, Fernet.generate_key().decode())


def _body(**extra):
    return {
        "name": "AWS Main",
        "provider": "bedrock",
        "values": {"aws_access_key_id": SECRET},
        "info": {"aws_region_name": "us-east-1"},
        "proxies": ["local-us1"],
        **extra,
    }


def _state(db):
    db.expire_all()
    return (
        sorted((c.name, c.provider, c.values_encrypted or "", str(c.credential_info)) for c in db.query(DBRegistryCredential)),
        sorted((link.credential_id, link.region_id) for link in db.query(DBRegistryCredentialRegion)),
    )


@pytest.fixture
def before(registry_db, writable):
    """The state with the credentials of the `writable` fixture."""
    return _state(registry_db)


def _main(db):
    return db.query(DBRegistryCredential).filter_by(name="AWS Main").one()


def test_create_list_patch_and_delete(client, admin_token, registry_db, before):
    created = ok(client.post(CREDENTIALS, json=_body(), headers=auth(admin_token)), 201)
    assert created == {
        "name": "AWS Main", "provider": "bedrock", "info": {"aws_region_name": "us-east-1"}, "proxies": ["local-us1"],
    }
    listed = client.get(CREDENTIALS, headers=auth(admin_token))
    assert [c["name"] for c in ok(listed)] == ["AWS Bedrock", "AWS Main", "c1", "c2"]
    assert created in ok(listed)
    assert listed.headers["X-Total-Count"] == "4"

    patched = ok(client.patch(
        f"{CREDENTIALS}/AWS Main",
        json={"values": {"aws_secret_access_key": "other-secret"}, "proxies": ["local-eu1", "local-us1"]},
        headers=auth(admin_token),
    ))
    assert patched["proxies"] == ["local-eu1", "local-us1"]
    row = _main(registry_db)
    assert decrypt_values(row.values_encrypted) == {
        "aws_access_key_id": SECRET, "aws_secret_access_key": "other-secret",
    }

    response = client.delete(f"{CREDENTIALS}/AWS Main", headers=auth(admin_token))
    assert response.status_code == 204
    assert _state(registry_db) == before


def test_no_response_and_no_column_holds_a_value(client, admin_token, registry_db, writable):
    responses = [
        client.post(CREDENTIALS, json=_body(), headers=auth(admin_token)),
        client.get(CREDENTIALS, headers=auth(admin_token)),
        client.patch(f"{CREDENTIALS}/AWS Main", json={"values": {"x": SECRET}}, headers=auth(admin_token)),
    ]
    assert all(SECRET not in response.text for response in responses)
    row = _main(registry_db)
    assert SECRET not in row.values_encrypted
    assert SECRET not in str(row.credential_info)


def test_a_missing_key_refuses_the_write(client, admin_token, registry_db, before, monkeypatch):
    monkeypatch.delenv(CREDENTIAL_KEYS_ENV)
    response = client.post(CREDENTIALS, json=_body(), headers=auth(admin_token))

    assert response.status_code == 409
    assert CREDENTIAL_KEYS_ENV in messages(response)[0]
    assert _state(registry_db) == before


def test_an_unknown_provider_proxy_and_a_secret_in_info_give_one_422(client, admin_token, registry_db, before):
    response = client.post(
        CREDENTIALS,
        json=_body(provider="nope", proxies=["local-us1", "local-xx"], info={"api_key": "sk-x"}),
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert messages(response) == [
        "Unknown provider nope",
        "info holds a secret; put secrets in values",
        "local-xx is not a proxy of the registry",
    ]
    assert _state(registry_db) == before


def test_a_duplicate_name_gives_409(client, admin_token, writable):
    ok(client.post(CREDENTIALS, json=_body(), headers=auth(admin_token)), 201)
    response = client.post(CREDENTIALS, json=_body(), headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["Credential already exists"]


def test_delete_is_refused_while_a_deployment_uses_the_name(client, admin_token, registry_db, before):
    # The claude-x deployment on local-us1 names AWS Bedrock.
    response = client.delete(f"{CREDENTIALS}/AWS Bedrock", headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["Deployments still use the credential"]
    assert _state(registry_db) == before


def test_a_proxy_is_not_removed_while_a_deployment_there_uses_the_name(client, admin_token, registry_db, before):
    response = client.patch(f"{CREDENTIALS}/AWS Bedrock", json={"proxies": []}, headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["A deployment on local-us1 uses the credential"]
    assert _state(registry_db) == before


def test_a_proxy_with_no_user_is_removed(client, admin_token, registry_db, writable):
    ok(client.post(CREDENTIALS, json=_body(), headers=auth(admin_token)), 201)
    # Another proxy's deployment with the name does not count.
    registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="eu-gpt").update(
        {"litellm_params": {"litellm_credential_name": "AWS Main"}}
    )
    registry_db.commit()

    assert ok(client.patch(f"{CREDENTIALS}/AWS Main", json={"proxies": []}, headers=auth(admin_token)))["proxies"] == []


def test_a_dry_run_saves_nothing(client, admin_token, registry_db, before):
    response = client.post(f"{CREDENTIALS}?dry_run=true", json=_body(), headers=auth(admin_token))

    body = ok(response)
    assert body["result"]["name"] == "AWS Main"
    # The plan shows the push to the proxy, never the values.
    assert [(s["proxy"], s["action"], s["credential"]) for s in body["steps"]] == [
        ("local-us1", "create_credential", "AWS Main"),
    ]
    assert SECRET not in response.text
    assert _state(registry_db) == before


def test_unknown_credential_gives_404(client, admin_token, writable):
    assert client.patch(f"{CREDENTIALS}/nope", json={"proxies": []}, headers=auth(admin_token)).status_code == 404
    assert client.delete(f"{CREDENTIALS}/nope", headers=auth(admin_token)).status_code == 404
