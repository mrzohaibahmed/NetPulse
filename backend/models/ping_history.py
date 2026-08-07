from utils.utc import utc_now


def create_ping_history(
    device_id,
    hostname,
    ip_address,
    status,
    response_time,
    scan_type="Manual"
):
    return {
        "deviceId": device_id,
        "hostname": hostname,
        "ipAddress": ip_address,
        "status": status,
        "responseTime": response_time,
        "scanType": scan_type,
        "timestamp": utc_now(),
    }
