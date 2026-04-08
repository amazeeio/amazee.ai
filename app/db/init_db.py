from app.db.models import Base, DBAPITokenExpiryOption
from app.db.database import engine, SessionLocal


def init_api_token_expiry_options():
    print("Initializing API token expiry options...")
    db = SessionLocal()
    try:
        options = [
            {"name": "1 day", "slug": "1_day", "days": 1},
            {"name": "1 week", "slug": "1_week", "days": 7},
            {"name": "1 month", "slug": "1_month", "days": 30},
            {"name": "2 months", "slug": "2_months", "days": 60},
            {"name": "3 months", "slug": "3_months", "days": 90},
            {"name": "4 months", "slug": "4_months", "days": 120},
            {"name": "5 months", "slug": "5_months", "days": 150},
            {"name": "6 months", "slug": "6_months", "days": 180},
            {"name": "7 months", "slug": "7_months", "days": 210},
            {"name": "8 months", "slug": "8_months", "days": 240},
            {"name": "9 months", "slug": "9_months", "days": 270},
            {"name": "10 months", "slug": "10_months", "days": 300},
            {"name": "11 months", "slug": "11_months", "days": 330},
            {"name": "1 year", "slug": "1_year", "days": 365},
            {"name": "forever", "slug": "forever", "days": None},
        ]

        for opt_data in options:
            existing = (
                db.query(DBAPITokenExpiryOption)
                .filter(DBAPITokenExpiryOption.slug == opt_data["slug"])
                .first()
            )
            if not existing:
                db_opt = DBAPITokenExpiryOption(**opt_data)
                db.add(db_opt)

        db.commit()
        print("API token expiry options initialized successfully!")
    except Exception as e:
        print(f"Error initializing API token expiry options: {e}")
        db.rollback()
    finally:
        db.close()


def init_db():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")
    init_api_token_expiry_options()


if __name__ == "__main__":
    init_db()
