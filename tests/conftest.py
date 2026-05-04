"""
Pytest configuration for liveness / enrollment integration tests.

Heavy ML libraries (insightface, faiss) are patched into sys.modules at
module-import time so the test suite can run without a full model
installation.  The DATABASE_URL env-var is redirected to a temporary
SQLite file that is created at session start and removed on exit.
"""

import contextlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# ── 1. Stub heavy ML dependencies BEFORE any faceapp module is imported ──────
for _stub in ("insightface", "insightface.app", "faiss", "jose"):
    sys.modules.setdefault(_stub, MagicMock())

# Stub bcrypt only when the real package is not importable (avoids SQLite
# serialisation errors with MagicMock objects when bcrypt IS installed).
try:
    import bcrypt as _bcrypt_check  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault("bcrypt", MagicMock())

# ── 2. Ensure faceapp root is importable ─────────────────────────────────────
_FACEAPP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _FACEAPP_ROOT not in sys.path:
    sys.path.insert(0, _FACEAPP_ROOT)

# ── 3. Redirect DATABASE_URL to a throwaway test file ────────────────────────
_TEST_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_liveness.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

# ── Standard imports (after path/env setup) ───────────────────────────────────
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import models

# ── Shared SQLAlchemy engine for the test suite ───────────────────────────────
_engine = create_engine(
    f"sqlite:///{_TEST_DB}",
    connect_args={"check_same_thread": False},
)


# ── Session-scoped DB lifecycle ───────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _db_lifecycle():
    """Create all tables once; drop them and remove the file after all tests."""
    models.Base.metadata.create_all(bind=_engine)
    yield
    models.Base.metadata.drop_all(bind=_engine)
    # Dispose the engine to release all SQLite file handles before deletion.
    _engine.dispose()
    try:
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
    except PermissionError:
        pass  # non-critical on Windows — file will be cleaned up on next run


@pytest.fixture(scope="module")
def db_session():
    """
    SQLAlchemy session shared across all tests in a module.
    Seeds the SystemSettings row (liveness_enabled=True by default) on first use.
    """
    Session = sessionmaker(bind=_engine)
    session = Session()
    if not session.query(models.SystemSettings).first():
        session.add(models.SystemSettings())
        session.commit()
    yield session
    session.close()


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def fixed_embedding():
    """Deterministic unit-normalised 512-d float32 vector (seed 42)."""
    rng = np.random.default_rng(42)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def client(db_session, fixed_embedding):
    """
    FastAPI TestClient with:
      - Test SQLite DB injected via dependency overrides
      - get_face_engine  → mock engine; get_embedding returns fixed_embedding
      - get_faiss_service → MagicMock (add_student, clear are no-ops)
      - security.encrypt_data → 64 zero bytes
      - utils.validate_image  → True
      - utils.get_image_hash  → "testhash"

    The TestClient is used as a context manager so the FastAPI lifespan
    (table creation, FAISS warm-up) executes inside the active patches.
    """
    from api.main import app, get_settings, get_current_user
    from db.database import get_db

    def _override_db():
        yield db_session

    def _override_settings():
        return db_session.query(models.SystemSettings).first()

    # Bypass JWT auth for all protected routes — mock as ADMIN (bypasses all role checks)
    _mock_admin = MagicMock(spec=models.User)
    _mock_admin.id = 1
    _mock_admin.username = "testadmin"
    _mock_admin.is_active = True
    _mock_admin.role = models.UserRole.ADMIN

    def _override_current_admin():
        return _mock_admin

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = _override_settings
    app.dependency_overrides[get_current_user] = _override_current_admin

    _mock_engine = MagicMock()
    _mock_engine.get_embedding.return_value = fixed_embedding
    _mock_faiss = MagicMock()

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("api.main.get_face_engine", return_value=_mock_engine)
        )
        stack.enter_context(
            patch("api.main.get_faiss_service", return_value=_mock_faiss)
        )
        # Keep crypto stubs consistent: enroll writes placeholder bytes,
        # verify reads back a deterministic embedding payload.
        stack.enter_context(
            patch("security.encrypt_data", return_value=b"\x00" * 64)
        )
        stack.enter_context(
            patch("security.decrypt_data", return_value=fixed_embedding.tobytes())
        )
        stack.enter_context(patch("utils.validate_image", return_value=True))
        stack.enter_context(patch("utils.get_image_hash", return_value="testhash"))
        stack.enter_context(
            patch(
                "api.main.fetch_student_from_external",
                new=AsyncMock(return_value={"full_name": "Test Student"}),
            )
        )

        with TestClient(app) as tc:
            yield tc

    app.dependency_overrides.clear()
