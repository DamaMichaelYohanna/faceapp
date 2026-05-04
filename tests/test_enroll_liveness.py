"""
Integration tests for POST /api/v1/enroll — liveness gating.

These tests use the FastAPI TestClient wired to a temporary SQLite
database with all heavy ML dependencies mocked (see conftest.py).
InsightFace and FAISS are NOT required to run this suite.

Scenarios covered
-----------------
1. Liveness passes   — main frame + 2 motion frames → 201
2. Liveness fails    — main frame + 3 identical frames → 400
3. Liveness guard    — single file is rejected when active liveness is enabled
4. No face detected  — mock engine returns None → 400
5. Excessive motion  — main + high-contrast phase-shift frames → 400
"""

import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.helpers import high_motion_frame, noisy_frame, sharp_frame


def _uid() -> str:
    """Return a short unique external_id string so each test creates its own student."""
    return f"TEST-{uuid.uuid4().hex[:8].upper()}"


class TestEnrollLiveness:
    """End-to-end liveness integration through POST /api/v1/enroll."""

    # ── Helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _enroll(tc: TestClient, external_id: str, *frames: bytes):
        """POST /api/v1/enroll with the given frames as a multipart file list."""
        files = [
            ("files", (f"frame_{i}.jpg", fb, "image/jpeg"))
            for i, fb in enumerate(frames)
        ]
        return tc.post("/api/v1/enroll", data={"external_id": external_id}, files=files)

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_liveness_passes_with_motion_frames(self, client):
        """
        Submitting 1 main frame + 2 liveness frames with natural motion:
          - LivenessDetector runs and passes (mean_diff in [0.15, 15.0], sharp)
          - 201 returned with liveness_passed=True and liveness_checked=True
        """
        response = self._enroll(
            client, _uid(),
            sharp_frame(),
            noisy_frame(seed=0),
            noisy_frame(seed=1),
        )
        body = response.json()
        assert response.status_code == 201, f"Unexpected body: {body}"
        assert body["success"] is True
        assert body["liveness_passed"] is True
        assert body["liveness_checked"] is True

    def test_liveness_fails_when_frames_are_static(self, client):
        """
        Submitting 3 identical frames triggers still-photo rejection:
          - mean_diff == 0 < STILL_PHOTO_THRESHOLD → LivenessDetector returns False
          - Endpoint returns 400 with actionable 'Liveness check failed' detail
        """
        frame = sharp_frame()
        response = self._enroll(client, _uid(), frame, frame, frame)
        assert response.status_code == 400
        assert "Liveness check failed" in response.json()["detail"]

    def test_single_file_rejected_when_liveness_frames_are_insufficient(self, client):
        """
        Active liveness requires multiple frames. A single upload is rejected:
          - 400 returned
          - detail explains that more frames are required
        """
        response = self._enroll(client, _uid(), sharp_frame())
        body = response.json()
        assert response.status_code == 400, f"Unexpected body: {body}"
        assert "requires at least" in body["detail"]

    def test_no_face_detected_returns_400(self, client):
        """
        When InsightFace returns None (no face found in the main frame):
          - 400 returned with 'No face detected' detail
        The inner patch shadows the fixture's mock only for this request.
        """
        _no_face_engine = MagicMock()
        _no_face_engine.get_embedding.return_value = None

        with patch("api.main.get_face_engine", return_value=_no_face_engine):
            response = self._enroll(
                client, _uid(),
                sharp_frame(),
                noisy_frame(seed=0),
                noisy_frame(seed=1),
            )

        assert response.status_code == 400
        assert "No face detected" in response.json()["detail"]

    def test_engine_failure_on_main_capture_returns_400(self, client):
        """
        If the face engine throws while processing the main capture,
        the API should return an actionable 400 instead of a generic 5xx.
        """
        engine = MagicMock()
        engine.get_embedding.side_effect = RuntimeError("embedding inference failed")

        with patch("api.main.get_face_engine", return_value=engine):
            response = self._enroll(
                client,
                _uid(),
                sharp_frame(),
                noisy_frame(seed=0),
                noisy_frame(seed=1),
            )

        assert response.status_code == 400
        assert "Could not process the captured image" in response.json()["detail"]

    def test_excessive_motion_fails_liveness(self, client):
        """
        Phase-shifted frames produce mean_diff >> EXCESSIVE_MOTION_THRESHOLD (15.0):
          - LivenessDetector returns False on the excessive-motion guard
          - 400 returned with 'Liveness check failed' detail
        """
        response = self._enroll(
            client, _uid(),
            sharp_frame(),
            high_motion_frame(),
            high_motion_frame(),
        )
        assert response.status_code == 400
        assert "Liveness check failed" in response.json()["detail"]

    def test_rejects_when_liveness_frames_have_no_detectable_face(self, client, fixed_embedding):
        """
        Main capture has a face, but liveness frames contain no detectable face:
          - consistency check must fail
          - API returns actionable out-of-frame guidance
        """
        engine = MagicMock()
        engine.get_embedding.side_effect = [fixed_embedding, None, None]

        with patch("api.main.get_face_engine", return_value=engine):
            response = self._enroll(
                client,
                _uid(),
                sharp_frame(),
                noisy_frame(seed=0),
                noisy_frame(seed=1),
            )

        assert response.status_code == 400
        assert "Face not detected in liveness frames" in response.json()["detail"]

    def test_engine_failure_on_liveness_frame_returns_400(self, client, fixed_embedding):
        """
        If the engine throws while processing a liveness frame,
        return a client-facing 400 instead of a generic server failure.
        """
        engine = MagicMock()
        engine.get_embedding.side_effect = [fixed_embedding, RuntimeError("frame decode failed")]

        with patch("api.main.get_face_engine", return_value=engine):
            response = self._enroll(
                client,
                _uid(),
                sharp_frame(),
                noisy_frame(seed=0),
                noisy_frame(seed=1),
            )

        assert response.status_code == 400
        assert "Could not process the liveness frame" in response.json()["detail"]

    def test_rejects_when_final_capture_does_not_match_liveness_person(self, client):
        """
        Simulate a bait-and-switch: main capture embedding differs strongly
        from embeddings extracted from liveness frames.
        """
        main_embedding = np.zeros(512, dtype=np.float32)
        main_embedding[0] = 1.0
        other_embedding = np.zeros(512, dtype=np.float32)
        other_embedding[1] = 1.0

        engine = MagicMock()
        engine.get_embedding.side_effect = [main_embedding, other_embedding, other_embedding]

        with patch("api.main.get_face_engine", return_value=engine):
            response = self._enroll(
                client,
                _uid(),
                sharp_frame(),
                noisy_frame(seed=5),
                noisy_frame(seed=6),
            )

        assert response.status_code == 400
        assert "does not match the live liveness frames" in response.json()["detail"]
