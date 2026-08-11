import ipaddress
import re

def parse_single_target(target: str) -> list[str]:
    """
    Parses a single target string (CIDR, hyphen range, or single IP)
    into a list of IP address strings. Returns an empty list on invalid input.
    """
    target = target.strip()
    if not target:
        return []

    # 1. Try CIDR block (e.g., 192.168.10.0/24)
    try:
        if '/' in target:
            net = ipaddress.IPv4Network(target, strict=False)
            return [str(ip) for ip in net.hosts()]
    except (ValueError, ipaddress.NetmaskValueError):
        pass

    # 2. Try hyphen range (e.g., 192.168.1.1-192.168.1.50 or 192.168.1.1-50)
    if '-' in target:
        parts = target.split('-')
        if len(parts) == 2:
            start_str = parts[0].strip()
            end_str = parts[1].strip()
            try:
                start_ip = ipaddress.IPv4Address(start_str)
                # Check if the end part is a full IP or just the last octet
                if '.' in end_str:
                    end_ip = ipaddress.IPv4Address(end_str)
                else:
                    # Replace the last octet of the start IP with end_str
                    octets = start_str.split('.')
                    octets[-1] = end_str
                    end_ip = ipaddress.IPv4Address('.'.join(octets))
                
                if start_ip <= end_ip:
                    start_int = int(start_ip)
                    end_int = int(end_ip)
                    # Limit range generation here to prevent memory spikes if they specify e.g. 10.0.0.1-10.255.255.254
                    if end_int - start_int + 1 > 2048:
                        raise ValueError("Range is too large.")
                    return [str(ipaddress.IPv4Address(ip)) for ip in range(start_int, end_int + 1)]
            except ValueError:
                pass

    # 3. Try single IP (e.g., 192.168.1.5)
    try:
        ip = ipaddress.IPv4Address(target)
        return [str(ip)]
    except ValueError:
        pass

    return []

def parse_scan_targets(targets_str: str) -> list[str]:
    """
    Parses a comma or space-separated list of targets into a sorted,
    deduplicated list of IP address strings.
    
    Raises:
        ValueError: If resolved unique IPs exceed 1024.
    """
    if not targets_str:
        return []

    # Replace newlines, tabs, commas, etc., with spaces, then split
    normalized = re.sub(r'[\r\n\t,;]+', ' ', targets_str)
    tokens = [t.strip() for t in normalized.split(' ') if t.strip()]

    ips = []
    for token in tokens:
        ips.extend(parse_single_target(token))

    # Deduplicate while preserving order, then sort by IPv4Address value
    seen = set()
    deduped = [x for x in ips if not (x in seen or seen.add(x))]

    if len(deduped) > 1024:
        raise ValueError("Resolved scan targets yield too many IP addresses. Maximum 1024 addresses per scan.")

    # Sort numerically by IP value
    try:
        deduped.sort(key=lambda ip: ipaddress.IPv4Address(ip))
    except ValueError:
        pass

    return deduped
