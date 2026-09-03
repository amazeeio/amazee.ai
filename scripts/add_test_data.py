#!/usr/bin/env python3

import os
import sys
from datetime import datetime, timedelta, UTC
import asyncio

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.db.models import (
    DBTeam,
    DBUser,
    DBRegion,
    DBPrivateAIKey,
)
from app.core.security import get_password_hash
from app.services.litellm import LiteLLMService


def create_test_data():
    """Create test data for teams and users"""

    # Create database session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        print("Creating test data...")

        # Check for existing test data on a case-by-case basis
        existing_teams = {}
        for team_name in [
            "Test Team 1",
            "Test Team 2 - Always Free",
            "Test Team 3 - Single User",
            "Test Team 4 - With Payment History",
            "Test Team 5 - 20 Days",
            "Test Team 6 - 95 Days",
            "Test Team 7 - 95 Days, Second",
            "Test Team 8 - 80 Days",
            "Test Team 9 - 80 Days, Second",
            "Test Team 10 - 75 Days",
            "Test Team 11 - 75 Days, Second",
            "Test Team 12 - 95 Days Retention Warning",
        ]:
            existing_team = db.query(DBTeam).filter(DBTeam.name == team_name).first()
            if existing_team:
                existing_teams[team_name] = existing_team
                print(f"⚠️  {team_name} already exists (ID: {existing_team.id})")
            else:
                print(f"✅ {team_name} will be created")

        # 1. Team with one user, created 32 days ago
        if "Test Team 1" not in existing_teams:
            print("\n1. Creating team with one user (created 32 days ago)...")
            team1 = DBTeam(
                name="Test Team 1",
                admin_email="admin1@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=32),
            )
            db.add(team1)
            db.commit()
            db.refresh(team1)

            user1 = DBUser(
                email="user1@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team1.id,
                created_at=datetime.now(UTC) - timedelta(days=32),
            )
            db.add(user1)
            db.commit()
            print(f"   Created team: {team1.name} (ID: {team1.id})")
            print(f"   Created user: {user1.email} (ID: {user1.id})")
        else:
            team1 = existing_teams["Test Team 1"]
            print(f"\n1. Team 1 already exists: {team1.name} (ID: {team1.id})")

        # 2. Team with one user, always_free=True, created 20 days ago
        if "Test Team 2 - Always Free" not in existing_teams:
            print(
                "\n2. Creating team with one user, always_free=True (created 20 days ago)..."
            )
            team2 = DBTeam(
                name="Test Team 2 - Always Free",
                admin_email="admin2@test.com",
                is_active=True,
                is_always_free=True,
                created_at=datetime.now(UTC) - timedelta(days=20),
            )
            db.add(team2)
            db.commit()
            db.refresh(team2)

            user2 = DBUser(
                email="user2@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team2.id,
                created_at=datetime.now(UTC) - timedelta(days=20),
            )
            db.add(user2)
            db.commit()
            print(
                f"   Created team: {team2.name} (ID: {team2.id}) - always_free: {team2.is_always_free}"
            )
            print(f"   Created user: {user2.email} (ID: {user2.id})")
        else:
            team2 = existing_teams["Test Team 2 - Always Free"]
            print(f"\n2. Team 2 already exists: {team2.name} (ID: {team2.id})")

        # 3. Team with one user
        if "Test Team 3 - Single User" not in existing_teams:
            print("\n3. Creating team with one user...")
            team3 = DBTeam(
                name="Test Team 3 - Single User",
                admin_email="admin3@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC),
            )
            db.add(team3)
            db.commit()
            db.refresh(team3)

            user3 = DBUser(
                email="user3@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team3.id,
                created_at=datetime.now(UTC),
            )
            db.add(user3)
            db.commit()

            print(f"   Created team: {team3.name} (ID: {team3.id})")
            print(f"   Created user: {user3.email} (ID: {user3.id})")
        else:
            team3 = existing_teams["Test Team 3 - Single User"]
            print(f"\n3. Team 3 already exists: {team3.name} (ID: {team3.id})")

        # 4. Team with one user, created 40 days ago, with payment 35 days ago
        if "Test Team 4 - With Payment History" not in existing_teams:
            print("\n4. Creating team with one user and payment history...")
            team4 = DBTeam(
                name="Test Team 4 - With Payment History",
                admin_email="admin4@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=40),
                last_payment=datetime.now(UTC) - timedelta(days=35),
            )
            db.add(team4)
            db.commit()
            db.refresh(team4)

            user4 = DBUser(
                email="user4@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team4.id,
                created_at=datetime.now(UTC) - timedelta(days=40),
            )
            db.add(user4)
            db.commit()

            print(f"   Created team: {team4.name} (ID: {team4.id})")
            print(f"   Created user: {user4.email} (ID: {user4.id})")
            print(f"   Payment made: {team4.last_payment.strftime('%Y-%m-%d')}")
        else:
            team4 = existing_teams["Test Team 4 - With Payment History"]
            print(f"\n4. Team 4 already exists: {team4.name} (ID: {team4.id})")

        # 5. Team with one user, created 20 days ago
        if "Test Team 5 - 20 Days" not in existing_teams:
            print("\n5. Creating team with one user (created 20 days ago)...")
            team5 = DBTeam(
                name="Test Team 5 - 20 Days",
                admin_email="admin5@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=20),
            )
            db.add(team5)
            db.commit()
            db.refresh(team5)

            user5 = DBUser(
                email="user5@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team5.id,
                created_at=datetime.now(UTC) - timedelta(days=20),
            )
            db.add(user5)
            db.commit()

            print(f"   Created team: {team5.name} (ID: {team5.id})")
            print(f"   Created user: {user5.email} (ID: {user5.id})")
        else:
            team5 = existing_teams["Test Team 5 - 20 Days"]
            print(f"\n5. Team 5 already exists: {team5.name} (ID: {team5.id})")

        # 6. Team created 95 days ago
        if "Test Team 6 - 95 Days" not in existing_teams:
            print("\n6. Creating team with one user (created 95 days ago)...")
            team6 = DBTeam(
                name="Test Team 6 - 95 Days",
                admin_email="admin6@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=95),
            )
            db.add(team6)
            db.commit()
            db.refresh(team6)

            user6 = DBUser(
                email="user6@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team6.id,
                created_at=datetime.now(UTC) - timedelta(days=95),
            )
            db.add(user6)
            db.commit()

            print(f"   Created team: {team6.name} (ID: {team6.id})")
            print(f"   Created user: {user6.email} (ID: {user6.id})")
        else:
            team6 = existing_teams["Test Team 6 - 95 Days"]
            print(f"\n6. Team 6 already exists: {team6.name} (ID: {team6.id})")

        # 7. Team created 95 days ago
        if "Test Team 7 - 95 Days, Second" not in existing_teams:
            print("\n7. Creating team with one user (created 95 days ago)...")
            team7 = DBTeam(
                name="Test Team 7 - 95 Days, Second",
                admin_email="admin7@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=95),
            )
            db.add(team7)
            db.commit()
            db.refresh(team7)

            user7 = DBUser(
                email="user7@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team7.id,
                created_at=datetime.now(UTC) - timedelta(days=95),
            )
            db.add(user7)
            db.commit()

            print(f"   Created team: {team7.name} (ID: {team7.id})")
            print(f"   Created user: {user7.email} (ID: {user7.id})")
        else:
            team7 = existing_teams["Test Team 7 - 95 Days, Second"]
            print(f"\n7. Team 7 already exists: {team7.name} (ID: {team7.id})")

        # 8. Team created 80 days ago
        if "Test Team 8 - 80 Days" not in existing_teams:
            print("\n8. Creating team with one user (created 80 days ago)...")
            team8 = DBTeam(
                name="Test Team 8 - 80 Days",
                admin_email="admin8@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=80),
            )
            db.add(team8)
            db.commit()
            db.refresh(team8)

            user8 = DBUser(
                email="user8@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team8.id,
                created_at=datetime.now(UTC) - timedelta(days=80),
            )
            db.add(user8)
            db.commit()

            print(f"   Created team: {team8.name} (ID: {team8.id})")
            print(f"   Created user: {user8.email} (ID: {user8.id})")
        else:
            team8 = existing_teams["Test Team 8 - 80 Days"]
            print(f"\n8. Team 8 already exists: {team8.name} (ID: {team8.id})")

        # 9. Team created 80 days ago
        if "Test Team 9 - 80 Days, Second" not in existing_teams:
            print("\n9. Creating team with one user (created 80 days ago)...")
            team9 = DBTeam(
                name="Test Team 9 - 80 Days, Second",
                admin_email="admin9@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=80),
            )
            db.add(team9)
            db.commit()
            db.refresh(team9)

            user9 = DBUser(
                email="user9@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team9.id,
                created_at=datetime.now(UTC) - timedelta(days=80),
            )
            db.add(user9)
            db.commit()

            print(f"   Created team: {team9.name} (ID: {team9.id})")
            print(f"   Created user: {user9.email} (ID: {user9.id})")
        else:
            team9 = existing_teams["Test Team 9 - 80 Days, Second"]
            print(f"\n9. Team 9 already exists: {team9.name} (ID: {team9.id})")

        # 10. Team created 75 days ago
        if "Test Team 10 - 75 Days" not in existing_teams:
            print("\n10. Creating team with one user (created 75 days ago)...")
            team10 = DBTeam(
                name="Test Team 10 - 75 Days",
                admin_email="admin10@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=75),
            )
            db.add(team10)
            db.commit()
            db.refresh(team10)

            user10 = DBUser(
                email="user10@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team10.id,
                created_at=datetime.now(UTC) - timedelta(days=75),
            )
            db.add(user10)
            db.commit()

            print(f"   Created team: {team10.name} (ID: {team10.id})")
            print(f"   Created user: {user10.email} (ID: {user10.id})")
        else:
            team10 = existing_teams["Test Team 10 - 75 Days"]
            print(f"\n10. Team 10 already exists: {team10.name} (ID: {team10.id})")

        # 11. Team created 75 days ago
        if "Test Team 11 - 75 Days, Second" not in existing_teams:
            print("\n11. Creating team with one user (created 75 days ago)...")
            team11 = DBTeam(
                name="Test Team 11 - 75 Days, Second",
                admin_email="admin11@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=75),
            )
            db.add(team11)
            db.commit()
            db.refresh(team11)

            user11 = DBUser(
                email="user11@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team11.id,
                created_at=datetime.now(UTC) - timedelta(days=75),
            )
            db.add(user11)
            db.commit()

            print(f"   Created team: {team11.name} (ID: {team11.id})")
            print(f"   Created user: {user11.email} (ID: {user11.id})")
        else:
            team11 = existing_teams["Test Team 11 - 75 Days, Second"]
            print(f"\n11. Team 11 already exists: {team11.name} (ID: {team11.id})")

        # 12. Team created 95 days ago, retention warning sent 14 days ago
        if "Test Team 12 - 95 Days Retention Warning" not in existing_teams:
            print(
                "\n12. Creating team with one user (created 95 days ago, retention warning sent 14 days ago)..."
            )
            team12 = DBTeam(
                name="Test Team 12 - 95 Days Retention Warning",
                admin_email="admin12@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=95),
                retention_warning_sent_at=datetime.now(UTC) - timedelta(days=14),
            )
            db.add(team12)
            db.commit()
            db.refresh(team12)

            user12 = DBUser(
                email="user12@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="admin",
                team_id=team12.id,
                created_at=datetime.now(UTC) - timedelta(days=95),
            )
            db.add(user12)
            db.commit()

            print(f"   Created team: {team12.name} (ID: {team12.id})")
            print(f"   Created user: {user12.email} (ID: {user12.id})")
            print(
                f"   Retention warning sent: {team12.retention_warning_sent_at.strftime('%Y-%m-%d')}"
            )
        else:
            team12 = existing_teams["Test Team 12 - 95 Days Retention Warning"]
            print(f"\n12. Team 12 already exists: {team12.name} (ID: {team12.id})")

        print("\n✅ Test data created successfully!")
        print("\nSummary:")
        print(f"- Team 1: {team1.name} (created 32 days ago)")
        print(f"- Team 2: {team2.name} (always_free=True, created 20 days ago)")
        print(f"- Team 3: {team3.name} ()")
        print(f"- Team 4: {team4.name} (payment history, created 40 days ago)")
        print(f"- Team 5: {team5.name} (created 20 days ago)")
        print(f"- Team 6: {team6.name} (created 95 days ago)")
        print(f"- Team 7: {team7.name} (created 95 days ago)")
        print(f"- Team 8: {team8.name} (created 80 days ago)")
        print(f"- Team 9: {team9.name} (created 80 days ago)")
        print(f"- Team 10: {team10.name} (created 75 days ago)")
        print(f"- Team 11: {team11.name} (created 75 days ago)")
        print(
            f"- Team 12: {team12.name} (created 95 days ago, retention warning sent 14 days ago)"
        )
        print("- Total users created: 12")

    except Exception as e:
        print(f"❌ Error creating test data: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()


async def create_test_keys(count: int):
    # Create database session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        team = db.query(DBTeam).first()
        region = db.query(DBRegion).filter(DBRegion.is_active.is_(True)).first()
        if not region:
            print("No active regions, not creating test keys")
            return

        # Check if test keys already exist
        existing_key = (
            db.query(DBPrivateAIKey)
            .filter(DBPrivateAIKey.name == "auto_test_0")
            .first()
        )
        if existing_key:
            print(
                "⚠️  Test keys already exist (found auto_test_0), skipping key creation"
            )
            return

        print(f"Creating {count} test keys for team {team.name}...")
        litellm = LiteLLMService(region.litellm_api_url, region.litellm_api_key)
        team_id = team.id
        for i in range(0, count):
            key_name = f"auto_test_{i}"
            litellm_token = await litellm.create_key(
                email=team.admin_email,
                name=key_name,
                user_id=team_id,
                team_id=LiteLLMService.format_team_id(region.name, team_id),
            )

            # Create response object
            db_token = DBPrivateAIKey(
                litellm_token=litellm_token,
                litellm_api_url=region.litellm_api_url,
                owner_id=None,
                team_id=team_id,
                name=key_name,
                region_id=region.id,
            )
            db.add(db_token)
            db.commit()
            print(f"Created LLM token {key_name} in team {team.name}")

    except Exception as e:
        print(f"failed to create test keys {str(e)}")


def main():
    """Main function to run the script"""
    try:
        create_test_data()
        asyncio.run(create_test_keys(50))
    except Exception as e:
        print(f"Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
