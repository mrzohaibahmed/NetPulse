"""Unit tests for storm incident HTTP serialization."""

from __future__ import annotations

import json
import unittest
from datetime import datetime

from bson import ObjectId
from flask import Flask

from services.storm.diagnostics.serializer import serialize_incident


class IncidentSerializerTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId()
        self.doc = {
            "_id": ObjectId(),
            "incidentId": "storm-2026-000099",
            "deviceId": self.device_id,
            "interface": "Gi1/0/5",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "MITIGATED",
            "severity": "HIGH",
            "incidentType": "STORM",
            "createdAt": datetime(2026, 7, 31, 9, 0, 0),
            "updatedAt": datetime(2026, 7, 31, 9, 5, 0),
            "stabilizationEnd": datetime(2026, 7, 31, 9, 10, 0),
            "recoveryRetryCount": 1,
            "trigger": {"risk": 80.0, "confirmation": True, "safety": True},
            "statistics": {
                "broadcastRate": 100.0,
                "latestSample": {
                    "broadcastPackets": 1,
                    "timestamp": datetime(2026, 7, 31, 8, 59, 0),
                },
            },
            "eligibility": {
                "_id": ObjectId(),
                "deviceId": self.device_id,
                "interface": "Gi1/0/5",
                "eligible": True,
                "reason": "Access Port",
                "failedRule": None,
                "confidence": 100,
                "checks": {"monitoring": True},
                "timestamp": datetime(2026, 7, 31, 8, 58, 0),
            },
            "risk": {
                "_id": ObjectId(),
                "deviceId": self.device_id,
                "interface": "Gi1/0/5",
                "riskScore": 80.0,
                "severity": "HIGH",
                "contributors": [],
                "rawMetrics": {},
                "timestamp": datetime(2026, 7, 31, 8, 58, 30),
            },
            "confirmation": {
                "_id": ObjectId(),
                "deviceId": self.device_id,
                "interface": "Gi1/0/5",
                "confirmed": True,
                "state": "CONFIRMED",
                "consecutiveHighSamples": 4,
                "requiredSamples": 4,
                "timestamp": datetime(2026, 7, 31, 8, 59, 0),
            },
            "safety": {
                "_id": ObjectId(),
                "deviceId": self.device_id,
                "interface": "Gi1/0/5",
                "safe": True,
                "reason": "All safety checks passed",
                "failedRule": None,
                "checks": {"stormConfirmed": True},
                "timestamp": datetime(2026, 7, 31, 8, 59, 30),
            },
            "timeline": [
                {
                    "event": "Risk Calculated",
                    "time": datetime(2026, 7, 31, 8, 58, 30),
                    "detail": "risk=80.0",
                }
            ],
        }

    def test_serialize_incident_is_json_safe(self):
        payload = serialize_incident(self.doc)
        # Must not raise TypeError for datetime/ObjectId
        encoded = json.dumps(payload)
        self.assertIn("storm-2026-000099", encoded)

        self.assertEqual(payload["deviceId"], str(self.device_id))
        self.assertIsInstance(payload["createdAt"], str)
        self.assertTrue(payload["createdAt"].endswith("Z"))
        self.assertIsInstance(payload["statistics"]["latestSample"]["timestamp"], str)
        self.assertIsInstance(payload["eligibility"]["_id"], str)
        self.assertIsInstance(payload["eligibility"]["deviceId"], str)
        self.assertIsInstance(payload["eligibility"]["timestamp"], str)
        self.assertIsInstance(payload["risk"]["timestamp"], str)
        self.assertIsInstance(payload["confirmation"]["timestamp"], str)
        self.assertIsInstance(payload["safety"]["timestamp"], str)
        self.assertIsInstance(payload["timeline"][0]["time"], str)
        self.assertEqual(payload["recoveryRetryCount"], 1)
        self.assertIsInstance(payload["stabilizationEnd"], str)

    def test_flask_jsonify_list_incidents_payload(self):
        app = Flask(__name__)
        with app.app_context():
            from flask import jsonify

            payload = {
                "success": True,
                "data": [serialize_incident(self.doc)],
            }
            response = jsonify(payload)
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertTrue(body["success"])
            self.assertEqual(body["data"][0]["incidentId"], "storm-2026-000099")


if __name__ == "__main__":
    unittest.main()
