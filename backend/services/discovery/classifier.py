"""
Device Classification Engine
============================
Rule-based hostname resolution and device-type classification.

Accepts multi-signal evidence (vendor, MAC OUI, OS, ports, services,
fingerprints, and future SNMP/SSH/LLDP/CDP inputs) without redesign.

Discovery routes and nmap_service call ``classify_device``; classification
logic does not live in Flask routes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Canonical device types (inventory-facing)
# ---------------------------------------------------------------------------

DEVICE_TYPE_MANAGED_SWITCH = "Managed Switch"
DEVICE_TYPE_SWITCH = "Switch"
DEVICE_TYPE_ROUTER = "Router"
DEVICE_TYPE_FIREWALL = "Firewall"
DEVICE_TYPE_WINDOWS_PC = "Windows PC"
DEVICE_TYPE_WORKSTATION = "Workstation"
DEVICE_TYPE_LINUX_SERVER = "Linux Server"
DEVICE_TYPE_SERVER = "Server"
DEVICE_TYPE_HYPERVISOR = "Hypervisor"
DEVICE_TYPE_NAS = "NAS"
DEVICE_TYPE_PRINTER = "Printer"
DEVICE_TYPE_IP_CAMERA = "IP Camera"
DEVICE_TYPE_ACCESS_POINT = "Access Point"
DEVICE_TYPE_UNKNOWN = "Unknown Device"

UNKNOWN_HOSTNAMES = frozenset({
    "",
    "unknown",
    "unknown device",
    "localhost",
    "localhost.localdomain",
})


@dataclass
class ClassificationEvidence:
    """
    Extensible evidence bag for classification.

    Future sources (SNMP, LLDP, CDP, topology) can populate ``extra`` or
    dedicated optional fields without changing the classifier API.
    """

    ip_address: str = ""
    hostname_ptr: str = ""
    hostname_service: str = ""
    hostname_ssh: str = ""
    hostname_existing: str = ""
    vendor: str = ""
    mac_vendor: str = ""
    os_name: str = ""
    os_family: str = ""
    os_generation: str = ""
    nmap_device_type: str = ""
    ports: list[dict[str, Any]] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassificationResult:
    hostname: str
    vendor: str
    operating_system: str
    device_type: str
    confidence: int
    classification_method: str
    discovery_source: str
    signals_matched: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _RuleMatch:
    device_type: str
    score: int
    method: str
    signals: tuple[str, ...]


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _lower(value: Any) -> str:
    return _norm(value).lower()


def is_unknown_hostname(hostname: str | None) -> bool:
    return _lower(hostname) in UNKNOWN_HOSTNAMES


def evidence_from_network_info(
    network_info: dict | None,
    *,
    ip_address: str = "",
    existing_hostname: str = "",
    hostname_ssh: str = "",
    extra: dict | None = None,
) -> ClassificationEvidence:
    """Build evidence from an nmap ``networkInfo`` document."""
    info = network_info or {}
    os_block = info.get("os") or {}
    ports = list(info.get("ports") or [])
    services = list(info.get("services") or [])
    products: list[str] = []
    for port in ports:
        product = _norm(port.get("product"))
        if product:
            products.append(product)
        extra_info = _norm(port.get("extraInfo"))
        if extra_info:
            products.append(extra_info)

    hostname_service = _hostname_from_services(ports, services, products)

    return ClassificationEvidence(
        ip_address=ip_address or "",
        hostname_ptr=_norm(info.get("hostname")),
        hostname_service=hostname_service,
        hostname_ssh=_norm(hostname_ssh),
        hostname_existing=_norm(existing_hostname),
        vendor=_norm(info.get("vendor")),
        mac_vendor=_norm(info.get("vendor")),
        os_name=_norm(os_block.get("name")),
        os_family=_norm(os_block.get("family")),
        os_generation=_norm(os_block.get("generation")),
        nmap_device_type=_norm(info.get("deviceType")),
        ports=ports,
        services=services,
        products=products,
        extra=dict(extra or {}),
    )


def _hostname_from_services(
    ports: list[dict],
    services: list[str],
    products: list[str],
) -> str:
    """
    Priority 2: pull a hostname-like token from service banners / products.

    Looks for common patterns in product + extraInfo (e.g. SSL CN=, SMB name).
    """
    blobs: list[str] = []
    for port in ports:
        for key in ("product", "extraInfo", "version"):
            value = _norm(port.get(key))
            if value:
                blobs.append(value)
    blobs.extend(_norm(s) for s in services)
    blobs.extend(_norm(p) for p in products)

    patterns = (
        re.compile(r"(?:CN|computer\s*name|hostname)\s*[=:]\s*([A-Za-z0-9._-]+)", re.I),
        re.compile(r"\b([A-Za-z0-9][A-Za-z0-9._-]{1,62})\.(?:local|lan|internal|home)\b", re.I),
    )
    for blob in blobs:
        for pattern in patterns:
            match = pattern.search(blob)
            if match:
                candidate = match.group(1).strip(".")
                if candidate and not is_unknown_hostname(candidate):
                    return candidate
    return ""


def resolve_hostname(evidence: ClassificationEvidence) -> tuple[str, str]:
    """
    Hostname priority:
      1. Reverse DNS (nmap PTR)
      2. Nmap service information
      3. SSH-configured hostname
      4. Unknown (never preferred over an existing real hostname)
    """
    candidates = (
        (evidence.hostname_ptr, "nmap-ptr"),
        (evidence.hostname_service, "nmap-service"),
        (evidence.hostname_ssh, "ssh"),
    )
    for name, source in candidates:
        if name and not is_unknown_hostname(name):
            return name, source

    if evidence.hostname_existing and not is_unknown_hostname(evidence.hostname_existing):
        return evidence.hostname_existing, "existing"

    return "Unknown", "none"


def _open_ports(evidence: ClassificationEvidence) -> set[int]:
    result: set[int] = set()
    for port in evidence.ports:
        if _lower(port.get("state")) != "open":
            continue
        try:
            result.add(int(port.get("port")))
        except (TypeError, ValueError):
            continue
    return result


def _service_names(evidence: ClassificationEvidence) -> set[str]:
    names = {_lower(s) for s in evidence.services if s}
    for port in evidence.ports:
        if _lower(port.get("state")) == "open":
            svc = _lower(port.get("service"))
            if svc:
                names.add(svc)
    return names


def _haystack(evidence: ClassificationEvidence) -> str:
    parts = [
        evidence.vendor,
        evidence.mac_vendor,
        evidence.os_name,
        evidence.os_family,
        evidence.os_generation,
        evidence.nmap_device_type,
        " ".join(evidence.services),
        " ".join(evidence.products),
        _norm(evidence.extra.get("snmpSysDescr")),
        _norm(evidence.extra.get("lldpSystemDescription")),
        _norm(evidence.extra.get("cdpPlatform")),
    ]
    for port in evidence.ports:
        parts.append(_norm(port.get("product")))
        parts.append(_norm(port.get("extraInfo")))
        parts.append(_norm(port.get("service")))
    return " | ".join(p for p in parts if p).lower()


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(n in text for n in needles)


def _evaluate_rules(evidence: ClassificationEvidence) -> _RuleMatch | None:
    """
    Apply ordered classification rules. Higher score wins; first max wins.
    """
    hay = _haystack(evidence)
    ports = _open_ports(evidence)
    services = _service_names(evidence)
    matches: list[_RuleMatch] = []

    # --- Printer (JetDirect / IPP / known printer vendors) ---
    printer_ports = bool(ports & {9100, 515, 631})
    printer_vendor = _contains_any(
        hay,
        ("hewlett", "hp inc", "hp ", "canon", "epson", "brother", "xerox", "ricoh", "kyocera", "lexmark"),
    )
    printer_service = bool(services & {"printer", "ipp", "jetdirect", "pdl-datastream", "printer_raw"})
    if printer_ports or printer_service or ("printer" in evidence.nmap_device_type.lower()):
        score = 55
        signals = ["ports-or-service"]
        if printer_ports:
            score += 25
            signals.append("port-9100/515/631")
        if printer_vendor:
            score += 15
            signals.append("vendor")
        if printer_service:
            score += 5
            signals.append("service")
        matches.append(_RuleMatch(DEVICE_TYPE_PRINTER, min(score, 98), "printer-fingerprint", tuple(signals)))

    # --- IP Camera ---
    camera_vendor = _contains_any(
        hay,
        ("hikvision", "dahua", "axis communications", "uniview", "reolink", "foscam", "hanwha", "vivotek"),
    )
    camera_ports = bool(ports & {554, 8000, 37777, 34567})
    if camera_vendor or (camera_ports and _contains_any(hay, ("camera", "ipcam", "nvr", "dvr", "rtsp"))):
        score = 50
        signals: list[str] = []
        if camera_vendor:
            score += 35
            signals.append("vendor")
        if camera_ports:
            score += 15
            signals.append("ports")
        if "camera" in hay or "rtsp" in services:
            score += 5
            signals.append("fingerprint")
        matches.append(_RuleMatch(DEVICE_TYPE_IP_CAMERA, min(score, 98), "ip-camera-fingerprint", tuple(signals or ["ports"])))

    # --- NAS ---
    nas_vendor = _contains_any(
        hay,
        ("synology", "qnap", "truenas", "freenas", "netgear", "diskstation"),
    )
    if nas_vendor or ("diskstation" in hay):
        score = 60
        signals = ["vendor"]
        if bool(ports & {5000, 5001, 548, 2049}):
            score += 25
            signals.append("ports")
        if bool(services & {"nfs", "afp", "smb", "microsoft-ds"}):
            score += 10
            signals.append("services")
        matches.append(_RuleMatch(DEVICE_TYPE_NAS, min(score, 98), "nas-fingerprint", tuple(signals)))

    # --- Hypervisor ---
    if _contains_any(hay, ("vmware", "esxi", "esx ", "vsphere", "proxmox", "xenserver", "hyper-v")):
        score = 70
        signals = ["os-or-vendor"]
        if _contains_any(hay, ("esxi", "esx", "vmware esx")):
            score += 25
            signals.append("esxi")
        if 443 in ports or 902 in ports or 903 in ports:
            score += 5
            signals.append("ports")
        matches.append(_RuleMatch(DEVICE_TYPE_HYPERVISOR, min(score, 99), "hypervisor-fingerprint", tuple(signals)))

    # --- Firewall ---
    if _contains_any(
        hay,
        ("fortinet", "fortigate", "palo alto", "firewall", "asa ", "firepower", "checkpoint", "sophos", "pfsense"),
    ) or "firewall" in evidence.nmap_device_type.lower():
        score = 70
        signals = ["vendor-or-os"]
        if bool(ports & {443, 10443, 8443}) or 22 in ports:
            score += 15
            signals.append("mgmt-ports")
        matches.append(_RuleMatch(DEVICE_TYPE_FIREWALL, min(score, 96), "firewall-fingerprint", tuple(signals)))

    # --- Access Point ---
    if _contains_any(hay, ("access point", "aironet", "aruba ap", "unifi", "ubiquiti", "wireless ap", "capwap")):
        score = 72
        signals = ["vendor-or-fingerprint"]
        matches.append(_RuleMatch(DEVICE_TYPE_ACCESS_POINT, min(score, 95), "access-point-fingerprint", tuple(signals)))

    # --- Cisco / network gear: Switch vs Router ---
    cisco_like = _contains_any(
        hay,
        ("cisco", "ios xe", "ios-xe", "cisco ios", "nx-os", "nxos", "catalyst", "nexus"),
    )
    routing_hints = _contains_any(
        hay,
        ("router", "isr", "asr", "routing", "ios xr", "csr1000"),
    ) or "router" in evidence.nmap_device_type.lower()
    switch_hints = _contains_any(
        hay,
        ("switch", "catalyst", "nexus", "layer2", "l2"),
    ) or "switch" in evidence.nmap_device_type.lower()
    has_ssh = 22 in ports or "ssh" in services
    has_snmp = 161 in ports or "snmp" in services

    if cisco_like or switch_hints or routing_hints:
        if routing_hints and not switch_hints:
            score = 70
            signals = ["routing-features"]
            if cisco_like:
                score += 15
                signals.append("vendor")
            if has_ssh:
                score += 10
                signals.append("ssh")
            matches.append(_RuleMatch(DEVICE_TYPE_ROUTER, min(score, 98), "cisco-router", tuple(signals)))
        elif switch_hints or (cisco_like and (has_ssh or has_snmp)):
            score = 65
            signals = ["switch-or-cisco"]
            if cisco_like:
                score += 15
                signals.append("vendor")
            if _contains_any(hay, ("ios", "nx-os", "nxos")):
                score += 10
                signals.append("os")
            if has_ssh:
                score += 8
                signals.append("ssh")
            if has_snmp:
                score += 5
                signals.append("snmp")
            # Managed switch when Cisco IOS + SSH (or SNMP)
            dtype = DEVICE_TYPE_MANAGED_SWITCH if cisco_like and (has_ssh or has_snmp) else DEVICE_TYPE_SWITCH
            matches.append(_RuleMatch(dtype, min(score, 98), "cisco-switch", tuple(signals)))
        elif routing_hints and switch_hints:
            # Prefer router when both, unless clearly L2-only
            matches.append(_RuleMatch(DEVICE_TYPE_ROUTER, 88, "cisco-mixed-routing", ("routing", "switch", "vendor")))

    # Generic router / switch from nmap class alone
    nmap_type = evidence.nmap_device_type.lower()
    if nmap_type == "router" and not any(m.device_type == DEVICE_TYPE_ROUTER for m in matches):
        matches.append(_RuleMatch(DEVICE_TYPE_ROUTER, 60, "nmap-osclass", ("nmap-deviceType",)))
    if nmap_type == "switch" and not any(
        m.device_type in (DEVICE_TYPE_SWITCH, DEVICE_TYPE_MANAGED_SWITCH) for m in matches
    ):
        matches.append(_RuleMatch(DEVICE_TYPE_SWITCH, 60, "nmap-osclass", ("nmap-deviceType",)))
    if nmap_type == "printer" and not any(m.device_type == DEVICE_TYPE_PRINTER for m in matches):
        matches.append(_RuleMatch(DEVICE_TYPE_PRINTER, 60, "nmap-osclass", ("nmap-deviceType",)))

    # --- Windows PC / Workstation ---
    windows = _contains_any(hay, ("windows", "microsoft"))
    smb = bool(ports & {139, 445}) or bool(services & {"microsoft-ds", "netbios-ssn", "smb"})
    rdp = 3389 in ports or "ms-wbt-server" in services or "rdp" in services
    if windows or (smb and rdp):
        score = 50
        signals = []
        if windows:
            score += 25
            signals.append("os")
        if smb:
            score += 15
            signals.append("smb")
        if rdp:
            score += 15
            signals.append("rdp")
        # Server vs PC: Windows Server keywords → Server
        if _contains_any(hay, ("windows server", "server 201", "server 202")):
            matches.append(_RuleMatch(DEVICE_TYPE_SERVER, min(score, 96), "windows-server", tuple(signals or ["os"])))
        else:
            matches.append(
                _RuleMatch(DEVICE_TYPE_WINDOWS_PC, min(score, 96), "windows-pc", tuple(signals or ["ports"]))
            )

    # --- Linux Server ---
    linux = _contains_any(hay, ("linux", "ubuntu", "debian", "centos", "redhat", "red hat", "fedora", "alpine"))
    has_ssh_only_mgmt = has_ssh and not smb and not rdp
    if linux or (has_ssh_only_mgmt and evidence.os_family.lower() in ("linux", "linux kernel")):
        score = 55
        signals = []
        if linux or evidence.os_family.lower().startswith("linux"):
            score += 25
            signals.append("os")
        if has_ssh:
            score += 15
            signals.append("ssh")
        # Web + SSH often a server
        if bool(ports & {80, 443}):
            score += 5
            signals.append("http")
        matches.append(_RuleMatch(DEVICE_TYPE_LINUX_SERVER, min(score, 95), "linux-server", tuple(signals or ["ssh"])))

    # Vendor-only weak match
    vendor = _lower(evidence.vendor or evidence.mac_vendor)
    if vendor and not matches:
        if _contains_any(vendor, ("cisco",)):
            matches.append(_RuleMatch(DEVICE_TYPE_MANAGED_SWITCH, 60, "vendor-only", ("vendor",)))
        elif _contains_any(vendor, ("hewlett", "hp ", "canon", "epson", "brother", "xerox")):
            matches.append(_RuleMatch(DEVICE_TYPE_PRINTER, 60, "vendor-only", ("vendor",)))
        elif _contains_any(vendor, ("hikvision", "dahua", "axis")):
            matches.append(_RuleMatch(DEVICE_TYPE_IP_CAMERA, 60, "vendor-only", ("vendor",)))
        elif _contains_any(vendor, ("synology", "qnap")):
            matches.append(_RuleMatch(DEVICE_TYPE_NAS, 60, "vendor-only", ("vendor",)))
        elif _contains_any(vendor, ("vmware",)):
            matches.append(_RuleMatch(DEVICE_TYPE_HYPERVISOR, 60, "vendor-only", ("vendor",)))
        elif _contains_any(vendor, ("microsoft",)):
            matches.append(_RuleMatch(DEVICE_TYPE_WINDOWS_PC, 55, "vendor-only", ("vendor",)))
        else:
            matches.append(_RuleMatch(DEVICE_TYPE_UNKNOWN, 40, "vendor-only-unknown", ("vendor",)))

    if not matches:
        return None

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[0]


def classify_device(evidence: ClassificationEvidence) -> ClassificationResult:
    """
    Classify a device from multi-signal evidence.

    Returns hostname, vendor, OS, device type, confidence, and method metadata.
    """
    hostname, host_source = resolve_hostname(evidence)
    vendor = _norm(evidence.vendor) or _norm(evidence.mac_vendor)
    operating_system = _norm(evidence.os_name)
    if not operating_system and evidence.os_family:
        operating_system = _norm(evidence.os_family)

    match = _evaluate_rules(evidence)

    if match is None:
        return ClassificationResult(
            hostname=hostname,
            vendor=vendor or "",
            operating_system=operating_system,
            device_type=DEVICE_TYPE_UNKNOWN,
            confidence=20,
            classification_method="unknown",
            discovery_source=host_source if host_source != "none" else "none",
            signals_matched=[],
        )

    confidence = int(match.score)
    # Soft floor/ceiling
    confidence = max(20, min(99, confidence))

    # Vendor-only agreement adjustment already encoded in rules.
    # Boost slightly when vendor + OS + ports all contributed.
    if len(match.signals) >= 3 and confidence < 98:
        confidence = min(98, confidence + 3)

    discovery_source = "nmap"
    if host_source == "ssh":
        discovery_source = "nmap+ssh"
    elif host_source == "nmap-ptr":
        discovery_source = "nmap"

    return ClassificationResult(
        hostname=hostname,
        vendor=vendor or "",
        operating_system=operating_system,
        device_type=match.device_type,
        confidence=confidence,
        classification_method=match.method,
        discovery_source=discovery_source,
        signals_matched=list(match.signals),
    )


def log_classification(
    logger: Any,
    ip_address: str,
    evidence: ClassificationEvidence,
    result: ClassificationResult,
) -> None:
    """Emit structured classification logs."""
    open_ports = sorted(_open_ports(evidence))
    ports_str = ",".join(str(p) for p in open_ports) if open_ports else "(none)"
    logger.info("Scanning %s", ip_address)
    logger.info("Vendor:\n%s", result.vendor or "(unknown)")
    logger.info("OS:\n%s", result.operating_system or "(unknown)")
    logger.info("Ports:\n%s", ports_str)
    logger.info("Classification:\n%s", result.device_type)
    logger.info("Confidence:\n%d%%", result.confidence)
