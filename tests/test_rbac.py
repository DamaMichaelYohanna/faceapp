from unittest.mock import MagicMock

from api.main import app, get_current_user
from db import models
from tests.helpers import noisy_frame, sharp_frame


def _set_current_role(role: models.UserRole) -> None:
    mock_user = MagicMock(spec=models.User)
    mock_user.id = 99
    mock_user.username = f"{role.value}_user"
    mock_user.is_active = True
    mock_user.role = role

    def _override_current_user():
        return mock_user

    app.dependency_overrides[get_current_user] = _override_current_user


def _enroll(client, external_id: str):
    files = [
        ("files", ("frame_0.jpg", sharp_frame(), "image/jpeg")),
        ("files", ("frame_1.jpg", noisy_frame(seed=0), "image/jpeg")),
        ("files", ("frame_2.jpg", noisy_frame(seed=1), "image/jpeg")),
    ]
    return client.post("/api/v1/enroll", data={"external_id": external_id}, files=files)


def _verify(client, identifier: str):
    files = [
        ("file", ("verify_0.jpg", sharp_frame(), "image/jpeg")),
        ("extra_frames", ("verify_1.jpg", noisy_frame(seed=2), "image/jpeg")),
        ("extra_frames", ("verify_2.jpg", noisy_frame(seed=3), "image/jpeg")),
    ]
    return client.post(f"/api/v1/verify/{identifier}", files=files)


class TestRoleBasedAccessControl:
    def test_capture_staff_can_enroll(self, client):
        _set_current_role(models.UserRole.CAPTURE_STAFF)

        response = _enroll(client, "RBAC-CAPTURE-001")

        assert response.status_code == 201
        assert response.json()["success"] is True

    def test_verify_staff_cannot_enroll(self, client):
        _set_current_role(models.UserRole.VERIFY_STAFF)

        response = _enroll(client, "RBAC-VERIFY-BLOCKED")

        assert response.status_code == 403
        assert response.json()["detail"] == "Operation not permitted"

    def test_capture_staff_cannot_verify(self, client):
        _set_current_role(models.UserRole.ADMIN)
        enroll_response = _enroll(client, "RBAC-VERIFY-READY")
        assert enroll_response.status_code == 201

        _set_current_role(models.UserRole.CAPTURE_STAFF)
        response = _verify(client, "RBAC-VERIFY-READY")

        assert response.status_code == 403
        assert response.json()["detail"] == "Operation not permitted"

    def test_verify_staff_can_verify(self, client):
        _set_current_role(models.UserRole.ADMIN)
        enroll_response = _enroll(client, "RBAC-VERIFY-OK")
        assert enroll_response.status_code == 201

        _set_current_role(models.UserRole.VERIFY_STAFF)
        response = _verify(client, "RBAC-VERIFY-OK")

        assert response.status_code == 200
        body = response.json()
        assert body["matched"] is True
        assert body["liveness_passed"] is True

    def test_admin_can_access_capture_and_verify(self, client):
        _set_current_role(models.UserRole.ADMIN)

        enroll_response = _enroll(client, "RBAC-ADMIN-001")
        verify_response = _verify(client, "RBAC-ADMIN-001")

        assert enroll_response.status_code == 201
        assert verify_response.status_code == 200