import argparse
import sys
import os

# Add the project root to the Python path if necessary
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.db.models import DBPrivateAIKey, DBUser, DBRegion


def delete_key(key_name: str, user_email: str = None, db_username: str = None, region_name: str = None):
    db: Session = SessionLocal()
    try:
        query = db.query(DBPrivateAIKey)

        if key_name:
            query = query.filter(DBPrivateAIKey.name == key_name)

        if user_email:
            user = db.query(DBUser).filter(DBUser.email == user_email).first()
            if not user:
                print(f"User with email '{user_email}' not found.")
                return
            query = query.filter(DBPrivateAIKey.owner_id == user.id)

        if db_username:
            query = query.filter(DBPrivateAIKey.database_username == db_username)

        if region_name:
            region = db.query(DBRegion).filter(DBRegion.name == region_name).first()
            if not region:
                print(f"Region with name '{region_name}' not found.")
                return
            query = query.filter(DBPrivateAIKey.region_id == region.id)

        keys_to_delete = query.options(joinedload(DBPrivateAIKey.region)).all()

        if not keys_to_delete:
            print("No keys found matching the provided criteria.")
            return

        print(f"Found {len(keys_to_delete)} key(s):")
        for key in keys_to_delete:
            region_str = key.region.name if key.region else "None"
            print(
                f"  - id: {key.id}, name: '{key.name}', owner_id: {key.owner_id}, db_username: '{key.database_username}', region: '{region_str}'"
            )

        confirm = input("\nAre you sure you want to delete these keys? (y/N): ")
        if confirm.lower() != "y":
            print("Operation cancelled.")
            return

        for key in keys_to_delete:
            region_str = key.region.name if key.region else "None"
            print(f"Deleting key: {key.name} (ID: {key.id}) from region: {region_str}")
            db.delete(key)

        db.commit()
        print(f"Successfully deleted {len(keys_to_delete)} key(s).")
    except Exception as e:
        db.rollback()
        print(f"An error occurred: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Force delete a Private AI Key from the database."
    )
    parser.add_argument("--name", required=True, help="Name of the key to delete")
    parser.add_argument("--email", help="Email of the key owner")
    parser.add_argument("--db-username", help="Database username of the key")
    parser.add_argument("--region", help="Region name of the key")

    args = parser.parse_args()
    delete_key(key_name=args.name, user_email=args.email, db_username=args.db_username, region_name=args.region)
