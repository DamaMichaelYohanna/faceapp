import base64
import json
from unittest.mock import MagicMock, patch

from tests.helpers import sharp_frame


def _as_data_url(jpeg_bytes: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg_bytes).decode('ascii')}"


class TestLivenessWebSocket:
    def test_ws_reports_face_present_when_detected(self, client, fixed_embedding):
        frame = _as_data_url(sharp_frame())
        engine = MagicMock()
        engine.get_embedding.return_value = fixed_embedding

        with patch("api.main.get_face_engine", return_value=engine):
            with client.websocket_connect("/ws/liveness") as ws:
                ws.send_text(json.dumps({"type": "analyze_frame", "payload": {"frame": frame}}))
                payload = json.loads(ws.receive_text())

        assert payload["type"] == "liveness_status"
        assert payload["payload"]["face_in_frame"] is True

    def test_ws_reports_out_of_frame_alert_when_face_missing(self, client):
        frame = _as_data_url(sharp_frame())
        engine = MagicMock()
        engine.get_embedding.return_value = None

        with patch("api.main.get_face_engine", return_value=engine):
            with client.websocket_connect("/ws/liveness") as ws:
                ws.send_text(json.dumps({"type": "analyze_frame", "payload": {"frame": frame}}))
                payload = json.loads(ws.receive_text())

        assert payload["type"] == "liveness_status"
        assert payload["payload"]["face_in_frame"] is False
        assert payload["payload"]["liveness_passed"] is False