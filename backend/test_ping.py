from services.ping_service import ping_device

# Test with Google DNS
result = ping_device("8.8.8.8")

print(result)

print("-" * 50)

# Test with Cloudflare DNS
result = ping_device("1.1.1.1")

print(result)

print("-" * 50)

# Test with an invalid IP
result = ping_device("192.168.250.250")

print(result)