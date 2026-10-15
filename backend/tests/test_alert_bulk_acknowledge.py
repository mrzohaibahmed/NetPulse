"""Bulk acknowledgement must cover the same active alerts as the alert list."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId
from flask import Flask

from routes.alert_routes import alert_bp
from utils.auth import create_access_token


def _client():
    app = Flask(__name__)
    app.register_blueprint(alert_bp, url_prefix="/api")
    return app.test_client()


def _headers():
    token = create_access_token(str(ObjectId()), "operator", "user")
    return {"Authorization": f"Bearer {token}"}


def test_bulk_acknowledge_requires_authentication():
    with patch("routes.alert_routes.db") as mock_db:
        response = _client().post("/api/alerts/acknowledge-all")

    assert response.status_code == 401
    mock_db.alerts.update_many.assert_not_called()


@patch("config.database.db.users.find_one", return_value={"active": True})
@patch("routes.alert_routes.db")
def test_bulk_acknowledges_only_active_alerts(mock_db, _mock_user):
    mock_db.alerts.update_many.return_value = MagicMock(modified_count=3)

    response = _client().post("/api/alerts/acknowledge-all", headers=_headers())

    assert response.status_code == 200
    assert response.get_json()["acknowledgedCount"] == 3
    active_filter, update = mock_db.alerts.update_many.call_args.args
    assert active_filter == {
        "dismissed": {"$ne": True},
        "acknowledged": {"$ne": True},
        "resolved": {"$ne": True},
    }
    assert update["$set"]["acknowledged"] is True
    assert isinstance(update["$set"]["acknowledgedAt"], datetime)


@patch("config.database.db.users.find_one", return_value={"active": True})
@patch("routes.alert_routes.db")
def test_bulk_acknowledge_with_no_active_alerts_is_safe(mock_db, _mock_user):
    mock_db.alerts.update_many.return_value = MagicMock(modified_count=0)

    response = _client().post("/api/alerts/acknowledge-all", headers=_headers())

    assert response.status_code == 200
    assert response.get_json()["acknowledgedCount"] == 0
