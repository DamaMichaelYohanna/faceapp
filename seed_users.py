"""
Seed demo users for each role.

Usage (from faceapp/ directory):
    python seed_users.py

Creates the following accounts if they don't already exist:

    Role            Username          Password
    ─────────────────────────────────────────────
    ADMIN           admin             admin123
    CAPTURE_STAFF   capture_staff     capture123
    VERIFY_STAFF    verify_staff      verify123

Re-running is safe — existing usernames are skipped.
"""

import sys
import os

# Ensure faceapp root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import database, models
import security

DEMO_USERS = [
    {
        "username": "admin",
        "password": os.getenv("ADMIN_PASSWORD", "admin123"),
        "role": models.UserRole.ADMIN,
    },
    {
        "username": "capture_staff",
        "password": os.getenv("CAPTURE_STAFF_PASSWORD", "capture123"),
        "role": models.UserRole.CAPTURE_STAFF,
    },
    {
        "username": "verify_staff",
        "password": os.getenv("VERIFY_STAFF_PASSWORD", "verify123"),
        "role": models.UserRole.VERIFY_STAFF,
    },
]


def seed():
    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()

    try:
        # Ensure SystemSettings row exists
        if not db.query(models.SystemSettings).first():
            db.add(models.SystemSettings())
            db.commit()
            print("  [+] SystemSettings initialised")

        for spec in DEMO_USERS:
            existing = (
                db.query(models.User)
                .filter(models.User.username == spec["username"])
                .first()
            )
            if existing:
                print(f"  [~] {spec['username']} ({spec['role'].value}) — already exists, skipped")
                continue

            user = models.User(
                username=spec["username"],
                hashed_password=security.get_password_hash(spec["password"]),
                role=spec["role"],
                is_active=True,
            )
            db.add(user)
            db.commit()
            print(f"  [+] {spec['username']} ({spec['role'].value}) — created (password: {spec['password']})")

    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding demo users...")
    seed()
    print("Done.")
