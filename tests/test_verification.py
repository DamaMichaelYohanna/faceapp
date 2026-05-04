"""
Integration tests for the verification and identification endpoints.

Coverage
--------
POST /api/v1/verify/{identifier}
    - Enrolled student with a matching face → 200 matched=True
    - Unknown / unenrolled identifier → 400
    - Liveness disabled, single frame → 200 matched=True
    - Liveness fails (static frames) → 200 matched=False liveness_passed=False
    - No face detected in submitted image → 400

POST /api/v1/identify
    - FAISS returns a confident match → 200 matched=True
    - FAISS returns no results → 200 matched=False
    - FAISS result below similarity threshold → 200 matched=False
    - No face detected → 400

POST /api/v1/liveness/check
    - Enough frames with motion → 200 liveness_passed=True
    - Static frames → 200 liveness_passed=False
    - Fewer than 3 frames → 400

All ML / crypto / external-API dependencies are mocked via conftest.py fixtures.
Tests share a module-scoped SQLAlchemy session and enrol a single student once.
"""

import io
import sys
import os

import pytest

# Ensure the tests/ directory is on sys.path so helpers.py is importable
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from unittest.mock import patch, MagicMock

from helpers import sharp_frame, noisy_frame, blurry_frame  # noqa: E402
from db import models
from core.liveness import LivenessDetector


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _jpeg_file(frame_bytes: bytes, name: str = "frame.jpg"):
    """Build a tuple suitable for requests multipart upload."""
    return (name, io.BytesIO(frame_bytes), "image/jpeg")


# ──────────────────────────────────────────────────────────────────────────────
# Module-scoped enrolled student fixture
# ──────────────────────────────────────────────────────────────────────────────

ENROLLED_ID = "VT-TEST-001"

@pytest.fixture
def enrolled_student(client, db_session):
    """Enrol ENROLLED_ID once for the whole verification test module."""
    from helpers import sharp_frame, noisy_frame

    resp = client.post(
        "/api/v1/enroll",
        data={"matric_number": ENROLLED_ID},
        files=[
            ("files", _jpeg_file(sharp_frame(), "f0.jpg")),
            ("files", _jpeg_file(noisy_frame(1), "f1.jpg")),
            ("files", _jpeg_file(noisy_frame(2), "f2.jpg")),
        ],
    )
    assert resp.status_code == 201, f"Enrolment failed: {resp.text}"
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/v1/verify/{identifier}
# ──────────────────────────────────────────────────────────────────────────────

class TestVerify:
    def test_enrolled_face_matches(self, client, enrolled_student, fixed_embedding):
        """Verify with the same embedding that was enrolled → matched=True."""
        with patch.object(LivenessDetector, "check_liveness", return_value=True):
            resp = client.post(
                f"/api/v1/verify/{ENROLLED_ID}",
                files=[
                    ("file", _jpeg_file(sharp_frame())),
                    ("extra_frames", _jpeg_file(noisy_frame(1), "e1.jpg")),
                    ("extra_frames", _jpeg_file(noisy_frame(2), "e2.jpg")),
                ],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is True
        assert body["mode"] == "1:1"
        assert body["student_id"] is not None
        assert body["confidence"] > 0.0

    def test_unknown_identifier_returns_400(self, client, enrolled_student):
        """Identifier that was never enrolled → 400."""

        resp = client.post(
            "/api/v1/verify/DOES_NOT_EXIST",
            files=[("file", _jpeg_file(sharp_frame()))],
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower() or "not enrolled" in resp.json()["detail"].lower()

    def test_liveness_disabled_single_frame_matches(self, client, db_session, enrolled_student):
        """When liveness is disabled a single frame should still verify cleanly."""

        settings = db_session.query(models.SystemSettings).first()
        original = settings.liveness_enabled
        settings.liveness_enabled = False
        db_session.commit()

        try:
            resp = client.post(
                f"/api/v1/verify/{ENROLLED_ID}",
                files=[("file", _jpeg_file(sharp_frame()))],
            )
            assert resp.status_code == 200
            assert resp.json()["matched"] is True
        finally:
            settings.liveness_enabled = original
            db_session.commit()

    def test_liveness_fail_returns_not_matched(self, client, db_session, enrolled_student):
        """Static (identical) frames fail liveness → matched=False, liveness_passed=False."""

        settings = db_session.query(models.SystemSettings).first()
        original = settings.liveness_enabled
        settings.liveness_enabled = True
        db_session.commit()

        try:
            # Patch liveness to return False regardless of frames
            with patch.object(LivenessDetector, "check_liveness", return_value=False):
                resp = client.post(
                    f"/api/v1/verify/{ENROLLED_ID}",
                    files=[
                        ("file", _jpeg_file(sharp_frame())),
                        ("extra_frames", _jpeg_file(sharp_frame(), "e1.jpg")),
                        ("extra_frames", _jpeg_file(sharp_frame(), "e2.jpg")),
                    ],
                )
            assert resp.status_code == 200
            body = resp.json()
            assert body["matched"] is False
            assert body["liveness_passed"] is False
        finally:
            settings.liveness_enabled = original
            db_session.commit()

    def test_no_face_detected_returns_400(self, client, enrolled_student):
        """If the face engine returns None → 400 no face detected."""

        with patch("api.main.get_face_engine") as mock_engine_factory:
            mock_engine = mock_engine_factory.return_value
            mock_engine.get_embedding.return_value = None
            resp = client.post(
                f"/api/v1/verify/{ENROLLED_ID}",
                files=[("file", _jpeg_file(sharp_frame()))],
            )

        assert resp.status_code == 400
        assert "face" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/v1/identify
# ──────────────────────────────────────────────────────────────────────────────

class TestIdentify:
    def test_faiss_match_above_threshold(self, client, db_session, enrolled_student):
        """FAISS returns a high-confidence match → matched=True."""

        student_id = enrolled_student["student_id"]
        settings = db_session.query(models.SystemSettings).first()

        with patch("api.main.get_faiss_service") as mock_faiss_factory, \
             patch.object(LivenessDetector, "check_liveness", return_value=True):
            mock_faiss = mock_faiss_factory.return_value
            mock_faiss.search.return_value = [
                {"student_id": student_id, "confidence": settings.similarity_threshold + 0.1}
            ]
            resp = client.post(
                "/api/v1/identify",
                files=[
                    ("file", _jpeg_file(sharp_frame())),
                    ("extra_frames", _jpeg_file(noisy_frame(1), "e1.jpg")),
                    ("extra_frames", _jpeg_file(noisy_frame(2), "e2.jpg")),
                ],
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is True
        assert body["mode"] == "1:N"
        assert body["student_id"] == student_id

    def test_faiss_no_results_returns_not_matched(self, client):
        """FAISS returns empty list → matched=False, no crash."""

        with patch("api.main.get_faiss_service") as mock_faiss_factory:
            mock_faiss = mock_faiss_factory.return_value
            mock_faiss.search.return_value = []
            resp = client.post(
                "/api/v1/identify",
                files=[("file", _jpeg_file(sharp_frame()))],
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is False
        assert body["mode"] == "1:N"

    def test_faiss_result_below_threshold_returns_not_matched(self, client, db_session, enrolled_student):
        """FAISS match below similarity threshold → matched=False."""

        student_id = enrolled_student["student_id"]
        settings = db_session.query(models.SystemSettings).first()

        with patch("api.main.get_faiss_service") as mock_faiss_factory:
            mock_faiss = mock_faiss_factory.return_value
            mock_faiss.search.return_value = [
                {"student_id": student_id, "confidence": settings.similarity_threshold - 0.1}
            ]
            resp = client.post(
                "/api/v1/identify",
                files=[("file", _jpeg_file(sharp_frame()))],
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is False

    def test_no_face_returns_400(self, client):
        """Face engine returns None for identification image → 400."""

        with patch("api.main.get_face_engine") as mock_engine_factory:
            mock_engine = mock_engine_factory.return_value
            mock_engine.get_embedding.return_value = None
            resp = client.post(
                "/api/v1/identify",
                files=[("file", _jpeg_file(sharp_frame()))],
            )

        assert resp.status_code == 400
        assert "face" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/v1/liveness/check
# ──────────────────────────────────────────────────────────────────────────────

class TestLivenessCheck:
    def test_motion_frames_pass(self, client):
        """3 frames with natural motion → liveness_passed=True."""

        with patch.object(LivenessDetector, "check_liveness", return_value=True):
            resp = client.post(
                "/api/v1/liveness/check",
                files=[
                    ("files", _jpeg_file(sharp_frame(), "f0.jpg")),
                    ("files", _jpeg_file(noisy_frame(1), "f1.jpg")),
                    ("files", _jpeg_file(noisy_frame(2), "f2.jpg")),
                ],
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["liveness_passed"] is True
        assert body["frames_analyzed"] == 3

    def test_static_frames_fail(self, client):
        """3 identical frames → liveness_passed=False."""

        with patch.object(LivenessDetector, "check_liveness", return_value=False):
            resp = client.post(
                "/api/v1/liveness/check",
                files=[
                    ("files", _jpeg_file(sharp_frame(), "f0.jpg")),
                    ("files", _jpeg_file(sharp_frame(), "f1.jpg")),
                    ("files", _jpeg_file(sharp_frame(), "f2.jpg")),
                ],
            )

        assert resp.status_code == 200
        assert resp.json()["liveness_passed"] is False

    def test_fewer_than_3_frames_rejected(self, client):
        """Only 2 frames submitted → 400 (below MIN_ACTIVE_LIVENESS_FRAMES)."""

        resp = client.post(
            "/api/v1/liveness/check",
            files=[
                ("files", _jpeg_file(sharp_frame(), "f0.jpg")),
                ("files", _jpeg_file(sharp_frame(), "f1.jpg")),
            ],
        )
        assert resp.status_code == 400
        assert "frame" in resp.json()["detail"].lower()
