from app.db.models import DBPrivateAIKey
from app.core.security import create_access_token

def test_list_private_ai_keys_force_user_keys(client, db, test_team, test_user, test_region):
    # Enable force_user_keys
    test_team.force_user_keys = True
    
    # Assign user to team
    test_user.team_id = test_team.id
    db.commit()

    # Create a user key
    user_key = DBPrivateAIKey(
        database_name="user-db",
        database_host="host",
        database_username="user",
        database_password="pass",
        litellm_token="user-token",
        owner_id=test_user.id,
        region_id=test_region.id,
        name="User Key"
    )
    db.add(user_key)

    # Create a shared team key (simulate existing one)
    team_key = DBPrivateAIKey(
        database_name="team-db",
        database_host="host",
        database_username="user",
        database_password="pass",
        litellm_token="team-token",
        team_id=test_team.id, # Team key
        region_id=test_region.id,
        name="Team Key"
    )
    db.add(team_key)
    db.commit()

    # Create token for user
    token = create_access_token(data={"sub": test_user.email})

    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    data = response.json()
    
    # Check what keys are returned
    key_names = [k["name"] for k in data]
    
    print(f"Keys returned: {key_names}")

    # Requirement: If force_user_keys is True, should NOT see "Team Key"
    assert "User Key" in key_names
    assert "Team Key" not in key_names
