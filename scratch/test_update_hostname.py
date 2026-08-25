import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from config.database import db
from bson import ObjectId

device = db.devices.find_one()
if not device:
    print("No device found")
    sys.exit(0)

print(f"Original: {device.get('hostname')}")
print(f"Identity Management: {device.get('identityManagement')}")

# Simulate PUT request
data = {"hostname": "Test-Name-Changed"}

from routes.device_routes import ownership_for_device_edit

update_data = {"hostname": data["hostname"]}
identity_updates = ownership_for_device_edit(device, update_data)
if identity_updates is not None:
    update_data["identityManagement"] = identity_updates

db.devices.update_one({"_id": device["_id"]}, {"$set": update_data})

updated = db.devices.find_one({"_id": device["_id"]})
print(f"Updated: {updated.get('hostname')}")
print(f"Identity Management: {updated.get('identityManagement')}")
