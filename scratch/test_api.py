import sys
import os
import requests
import time
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from config.database import db

# Insert a dummy device
db.devices.insert_one({
    "_id": "test_dev_123",
    "hostname": "Old-Name",
    "ipAddress": "1.1.1.1",
    "deviceType": "Router",
    "identityManagement": {"hostname": "auto"}
})

# Make API call directly to flask running app if possible... wait, maybe we can't.
