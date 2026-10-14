from app.registry.models import DBRegistryProvider
from tests.registry.api.conftest import auth


def test_providers_with_proxies_and_model_counts(client, admin_token, deployed, registry_db):
    registry_db.add(DBRegistryProvider(name="azure"))
    registry_db.commit()

    response = client.get("/registry/providers", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    assert response.json() == [
        {"provider": "azure", "proxies": [], "models": 0, "deployed_models": 0},
        {"provider": "bedrock", "proxies": ["local-us1"], "models": 1, "deployed_models": 1},
        # gpt-4o is on two proxies but counts once; gpt-old is not deployed.
        {"provider": "openai", "proxies": ["local-eu1", "local-us1"], "models": 2, "deployed_models": 1},
    ]
