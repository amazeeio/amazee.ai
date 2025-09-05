#!/usr/bin/env python3

import os
import sys
from datetime import datetime, timedelta, UTC
import random

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy.orm import sessionmaker, Session
from app.db.database import engine
from app.db.models import DBTeam, DBUser, DBProduct, DBTeamProduct
from app.core.security import get_password_hash

def create_test_data():
    """Create test data for teams, users, and products"""

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
            "Test Team 3 - With Product",
            "Test Team 4 - With Payment History",
            "Test Team 5 - No Products"
        ]:
            existing_team = db.query(DBTeam).filter(DBTeam.name == team_name).first()
            if existing_team:
                existing_teams[team_name] = existing_team
                print(f"⚠️  {team_name} already exists (ID: {existing_team.id})")
            else:
                print(f"✅ {team_name} will be created")

        # Check if products exist, create one if none exist
        existing_products = db.query(DBProduct).all()
        if not existing_products:
            print("No products found, creating a sample product...")
            sample_product = DBProduct(
                id="prod_test_sample",
                name="Test Sample Product",
                user_count=10,
                keys_per_user=5,
                total_key_count=50,
                service_key_count=10,
                max_budget_per_key=100.0,
                rpm_per_key=1000,
                vector_db_count=5,
                vector_db_storage=500,
                renewal_period_days=30,
                active=True,
                created_at=datetime.now(UTC)
            )
            db.add(sample_product)
            db.commit()
            db.refresh(sample_product)
            print(f"Created sample product: {sample_product.name} (ID: {sample_product.id})")
            existing_products = [sample_product]

        # Pick a random product for teams that need products
        selected_product = random.choice(existing_products)
        print(f"Selected product for teams: {selected_product.name} (ID: {selected_product.id})")

                # 1. Team with one user, created 32 days ago
        if "Test Team 1" not in existing_teams:
            print("\n1. Creating team with one user (created 32 days ago)...")
            team1 = DBTeam(
                name="Test Team 1",
                admin_email="admin1@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=32)
            )
            db.add(team1)
            db.commit()
            db.refresh(team1)

            user1 = DBUser(
                email="user1@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="user",
                team_id=team1.id,
                created_at=datetime.now(UTC) - timedelta(days=32)
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
            print("\n2. Creating team with one user, always_free=True (created 20 days ago)...")
            team2 = DBTeam(
                name="Test Team 2 - Always Free",
                admin_email="admin2@test.com",
                is_active=True,
                is_always_free=True,
                created_at=datetime.now(UTC) - timedelta(days=20)
            )
            db.add(team2)
            db.commit()
            db.refresh(team2)

            user2 = DBUser(
                email="user2@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="user",
                team_id=team2.id,
                created_at=datetime.now(UTC) - timedelta(days=20)
            )
            db.add(user2)
            db.commit()
            print(f"   Created team: {team2.name} (ID: {team2.id}) - always_free: {team2.is_always_free}")
            print(f"   Created user: {user2.email} (ID: {user2.id})")
        else:
            team2 = existing_teams["Test Team 2 - Always Free"]
            print(f"\n2. Team 2 already exists: {team2.name} (ID: {team2.id})")

                # 3. Team with one user and product association
        if "Test Team 3 - With Product" not in existing_teams:
            print("\n3. Creating team with one user and product association...")
            team3 = DBTeam(
                name="Test Team 3 - With Product",
                admin_email="admin3@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC)
            )
            db.add(team3)
            db.commit()
            db.refresh(team3)

            user3 = DBUser(
                email="user3@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="user",
                team_id=team3.id,
                created_at=datetime.now(UTC)
            )
            db.add(user3)
            db.commit()

            # Create team-product association
            team_product = DBTeamProduct(
                team_id=team3.id,
                product_id=selected_product.id
            )
            db.add(team_product)
            db.commit()

            print(f"   Created team: {team3.name} (ID: {team3.id})")
            print(f"   Created user: {user3.email} (ID: {user3.id})")
            print(f"   Associated with product: {selected_product.name} (ID: {selected_product.id})")
        else:
            team3 = existing_teams["Test Team 3 - With Product"]
            print(f"\n3. Team 3 already exists: {team3.name} (ID: {team3.id})")

                # 4. Team with one user, created 40 days ago, with payment 35 days ago, and product association
        if "Test Team 4 - With Payment History" not in existing_teams:
            print("\n4. Creating team with one user, payment history, and product association...")
            team4 = DBTeam(
                name="Test Team 4 - With Payment History",
                admin_email="admin4@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=40),
                last_payment=datetime.now(UTC) - timedelta(days=35)
            )
            db.add(team4)
            db.commit()
            db.refresh(team4)

            user4 = DBUser(
                email="user4@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="user",
                team_id=team4.id,
                created_at=datetime.now(UTC) - timedelta(days=40)
            )
            db.add(user4)
            db.commit()

            # Create team-product association
            team_product4 = DBTeamProduct(
                team_id=team4.id,
                product_id=selected_product.id
            )
            db.add(team_product4)
            db.commit()

            print(f"   Created team: {team4.name} (ID: {team4.id})")
            print(f"   Created user: {user4.email} (ID: {user4.id})")
            print(f"   Payment made: {team4.last_payment.strftime('%Y-%m-%d')}")
            print(f"   Associated with product: {selected_product.name} (ID: {selected_product.id})")
        else:
            team4 = existing_teams["Test Team 4 - With Payment History"]
            print(f"\n4. Team 4 already exists: {team4.name} (ID: {team4.id})")

                # 5. Team with one user, no products, created 20 days ago
        if "Test Team 5 - No Products" not in existing_teams:
            print("\n5. Creating team with one user, no products (created 20 days ago)...")
            team5 = DBTeam(
                name="Test Team 5 - No Products",
                admin_email="admin5@test.com",
                is_active=True,
                is_always_free=False,
                created_at=datetime.now(UTC) - timedelta(days=20)
            )
            db.add(team5)
            db.commit()
            db.refresh(team5)

            user5 = DBUser(
                email="user5@test.com",
                hashed_password=get_password_hash("testpassword123"),
                is_active=True,
                is_admin=False,
                role="user",
                team_id=team5.id,
                created_at=datetime.now(UTC) - timedelta(days=20)
            )
            db.add(user5)
            db.commit()

            print(f"   Created team: {team5.name} (ID: {team5.id})")
            print(f"   Created user: {user5.email} (ID: {user5.id})")
            print(f"   No products associated")
        else:
            team5 = existing_teams["Test Team 5 - No Products"]
            print(f"\n5. Team 5 already exists: {team5.name} (ID: {team5.id})")

        print("\n✅ Test data created successfully!")
        print(f"\nSummary:")
        print(f"- Team 1: {team1.name} (created 32 days ago)")
        print(f"- Team 2: {team2.name} (always_free=True, created 20 days ago)")
        print(f"- Team 3: {team3.name} (with product association)")
        print(f"- Team 4: {team4.name} (payment history, product association, created 40 days ago)")
        print(f"- Team 5: {team5.name} (no products, created 20 days ago)")
        print(f"- Total users created: 5")
        print(f"- Product used: {selected_product.name}")

    except Exception as e:
        print(f"❌ Error creating test data: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()

def main():
    """Main function to run the script"""
    try:
        create_test_data()
    except Exception as e:
        print(f"Script failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
