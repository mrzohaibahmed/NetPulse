"""
Extensible device-identification framework.

Phase 2 introduced the common contract and future extension points.
Phase 3 upgrades WindowsIdentifier from a stub to a live WMI/WinRM
implementation while preserving Nmap as the universal fallback.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from services.discovery.classifier import ClassificationResult, DEVICE_TYPE_UNKNOWN
from services.discovery.device_types import (
    CANONICAL_UNKNOWN,
    canonical_device_type,
    compact_identification_evidence,
)

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("identification")


@dataclass
class IdentificationContext:
    """Inputs available to an identifier without exposing secrets."""

    ip_address: str
    network_info: dict | None = None
    existing: dict | None = None
    try_ssh: bool = True
    preferred_device_type: str | None = None


@dataclass
class IdentificationResult:
    """
    Normalized identifier result used by the manager.

    ``classification`` carries the existing Phase 1 classification object for
    persistence so current MongoDB fields and APIs stay backward compatible.
    """

    success: bool
    method: str
    device_type: str = DEVICE_TYPE_UNKNOWN
    confidence: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    classification: ClassificationResult | None = None
    raw_evidence: Any = None


class BaseIdentifier(ABC):
    """Common identifier contract."""

    method_name = "base"

    def supports(self, context: IdentificationContext) -> bool:
        return True

    @abstractmethod
    def identify(self, context: IdentificationContext) -> IdentificationResult:
        """Return a normalized identification result."""


class NmapIdentifier(BaseIdentifier):
    """Adapter over the existing Nmap-backed classification flow."""

    method_name = "nmap"

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        if not context.network_info:
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error="Nmap networkInfo is unavailable",
                metadata={"implemented": True, "source": "networkInfo"},
            )

        try:
            from services.discovery.apply import classify_network_info  # noqa: PLC0415

            classification, evidence = classify_network_info(
                context.network_info,
                ip_address=context.ip_address,
                existing=context.existing,
                try_ssh=context.try_ssh,
            )
            classification.identification_method = self.method_name
            return IdentificationResult(
                success=True,
                method=self.method_name,
                device_type=classification.device_type,
                confidence=int(classification.confidence or 0),
                evidence=dict(getattr(classification, "identification_evidence", {}) or {}),
                metadata={
                    "implemented": True,
                    "source": "networkInfo",
                    "canonicalType": canonical_device_type(classification.device_type),
                },
                classification=classification,
                raw_evidence=evidence,
            )
        except Exception as exc:  # noqa: BLE001
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error=str(exc),
                metadata={"implemented": True, "source": "networkInfo"},
            )


class FutureStubIdentifier(BaseIdentifier):
    """Base class for future protocol identifiers."""

    not_implemented_reason = "Identifier not implemented yet"

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        return IdentificationResult(
            success=False,
            method=self.method_name,
            device_type=DEVICE_TYPE_UNKNOWN,
            confidence=0,
            error=self.not_implemented_reason,
            metadata={"implemented": False, "future": True},
        )


class WindowsIdentifier(BaseIdentifier):
    """
    WMI/WinRM-based identification for Windows PCs, laptops, and servers.

    Queries Win32_ComputerSystem, Win32_OperatingSystem,
    Win32_ComputerSystemProduct, and Win32_Processor via PowerShell over WinRM.

    Only runs when the device has WinRM credentials configured.
    On any failure, the IdentificationManager automatically falls through
    to the NmapIdentifier.
    """

    method_name = "wmi"

    def supports(self, context: IdentificationContext) -> bool:
        """True only when the device has WinRM credentials configured."""
        from services.wmi_service import has_winrm_credentials  # noqa: PLC0415

        return has_winrm_credentials(context.existing)

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        from services.wmi_service import (  # noqa: PLC0415
            has_winrm_credentials,
            is_winrm_available,
            query_windows_device,
        )
        from utils.secret_crypto import decrypt_secret  # noqa: PLC0415

        ip_address = context.ip_address
        existing = context.existing or {}

        if not is_winrm_available():
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error="pywinrm is not installed",
                metadata={"implemented": True, "source": "wmi"},
            )

        if not has_winrm_credentials(existing):
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error="WinRM credentials not configured",
                metadata={"implemented": True, "source": "wmi"},
            )

        creds = existing.get("credentials") or {}
        username = str(creds.get("winrmUsername") or "").strip()
        encrypted_password = creds.get("winrmPassword") or ""
        port = int(creds.get("winrmPort") or 5985)
        use_ssl = bool(creds.get("winrmUseSsl"))

        try:
            password = decrypt_secret(encrypted_password) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[WMI] Failed to decrypt WinRM password | host=%s", ip_address
            )
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error="Failed to decrypt WinRM credentials",
                metadata={"implemented": True, "source": "wmi"},
            )

        try:
            from config.database import WMI_TIMEOUT  # noqa: PLC0415

            wmi_info = query_windows_device(
                ip_address,
                username,
                password,
                port=port,
                use_ssl=use_ssl,
                timeout=WMI_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            # Sanitize error message — never include credentials
            error_msg = str(exc)
            if username and username in error_msg:
                error_msg = error_msg.replace(username, "***")
            if password and password in error_msg:
                error_msg = error_msg.replace(password, "***")
            logger.info(
                "[WMI] Identification failed | host=%s | error=%s",
                ip_address,
                error_msg[:200],
            )
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error=error_msg[:500],
                metadata={"implemented": True, "source": "wmi"},
            )

        # ── Determine device type from WMI OS info ─────────────────────
        os_caption = wmi_info.operating_system.lower()
        if "server" in os_caption:
            device_type = "Server"
            canonical = "SERVER"
        elif any(hint in os_caption for hint in ("windows 10", "windows 11")):
            device_type = existing.get("deviceType") or "Windows PC"
            canonical = canonical_device_type(device_type)
            if canonical == CANONICAL_UNKNOWN:
                canonical = "PC"
        else:
            device_type = existing.get("deviceType") or "Windows PC"
            canonical = canonical_device_type(device_type)
            if canonical == CANONICAL_UNKNOWN:
                canonical = "PC"

        # ── Build evidence payload ─────────────────────────────────────
        evidence = compact_identification_evidence(
            os_name=wmi_info.operating_system,
            os_family="Windows",
            vendor=wmi_info.manufacturer,
            hostname=wmi_info.hostname,
            manufacturer=wmi_info.manufacturer,
            model=wmi_info.model,
            serial_number=wmi_info.serial_number,
            os_version=wmi_info.os_version,
            os_build=wmi_info.os_build,
            system_uuid=wmi_info.system_uuid,
            cpu=wmi_info.cpu,
            total_ram_gb=wmi_info.total_ram_gb,
            signals=["wmi", "winrm"],
        )

        # WMI provides authoritative hardware data → high confidence.
        confidence = 97

        logger.info(
            "[WMI] Identification successful | host=%s | manufacturer=%s "
            "model=%s os=%s",
            ip_address,
            wmi_info.manufacturer or "(empty)",
            wmi_info.model or "(empty)",
            wmi_info.operating_system or "(empty)",
        )

        return IdentificationResult(
            success=True,
            method=self.method_name,
            device_type=device_type,
            confidence=confidence,
            evidence=evidence,
            metadata={
                "implemented": True,
                "source": "wmi",
                "canonicalType": canonical,
                "hostname": wmi_info.hostname,
            },
        )


class SNMPIdentifier(BaseIdentifier):
    """
    SNMP-based identification for managed network devices (Printers, Switches,
    Routers, Access Points, Firewalls, Network Devices).

    Queries System group (sysDescr, sysObjectID, sysUpTime, sysName) and ENTITY-MIB
    (entPhysicalTable) for hardware metadata and serial numbers.

    On any failure, the IdentificationManager automatically falls through
    to NmapIdentifier.
    """

    method_name = "snmp"

    def supports(self, context: IdentificationContext) -> bool:
        """
        True when:
        1. Device record has credentials.snmpCommunity configured, OR
        2. Nmap network_info indicates port 161 open or snmp service, OR
        3. Preferred device type is a managed network category (Printer, Switch, etc.)
        """
        from services.interface_collection.snmp import snmp_available  # noqa: PLC0415

        if not snmp_available():
            return False

        existing = context.existing or {}
        creds = existing.get("credentials") or {}
        if creds.get("snmpCommunity"):
            return True

        net_info = context.network_info or {}
        for port in net_info.get("ports") or []:
            if str(port.get("state") or "").lower() == "open":
                try:
                    if int(port.get("port") or 0) == 161:
                        return True
                except (TypeError, ValueError):
                    pass
        services = {str(s).lower() for s in (net_info.get("services") or []) if s}
        if "snmp" in services:
            return True

        preferred = canonical_device_type(
            context.preferred_device_type
            or (existing.get("identification") or {}).get("displayType")
            or existing.get("deviceType")
            or ""
        )
        return preferred in {
            "PRINTER",
            "SWITCH",
            "ROUTER",
            "FIREWALL",
            "ACCESS_POINT",
            "NETWORK_DEVICE",
        }

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        from services.interface_collection.snmp import (  # noqa: PLC0415
            SNMPCollectorError,
            collect_snmp_inventory,
            resolve_snmp_credentials,
            snmp_available,
        )

        ip_address = context.ip_address
        existing = context.existing or {}

        if not snmp_available():
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error="pysnmp is not installed",
                metadata={"implemented": True, "source": "snmp"},
            )

        try:
            device_record = dict(existing or {})
            if not device_record.get("ipAddress"):
                device_record["ipAddress"] = ip_address
            snmp_creds = resolve_snmp_credentials(device_record)
            snmp_info = collect_snmp_inventory(snmp_creds)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            enc_community = (existing.get("credentials") or {}).get("snmpCommunity") or ""
            raw_community = ""
            try:
                from utils.secret_crypto import decrypt_secret  # noqa: PLC0415
                raw_community = decrypt_secret(enc_community) if enc_community else ""
            except Exception:  # noqa: BLE001
                raw_community = ""

            for secret in (raw_community, enc_community):
                if secret and isinstance(secret, str) and secret in error_msg:
                    error_msg = error_msg.replace(secret, "***")

            logger.info(
                "[SNMP] Identification failed | host=%s | error=%s",
                ip_address,
                error_msg[:200],
            )
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error=error_msg[:500],
                metadata={"implemented": True, "source": "snmp"},
            )

        # ── Classify device using SNMP evidence ────────────────────────
        from services.discovery.classifier import (  # noqa: PLC0415
            ClassificationEvidence,
            classify_device,
        )

        evidence_bag = ClassificationEvidence(
            ip_address=ip_address,
            hostname_existing=str(existing.get("hostname") or "").strip(),
            hostname_service=snmp_info.sys_name,
            vendor=snmp_info.manufacturer or snmp_info.vendor_from_oid,
            os_name=snmp_info.sys_descr,
            extra={
                "snmpSysDescr": snmp_info.sys_descr,
                "sysObjectID": snmp_info.sys_object_id,
            },
        )
        if context.network_info:
            evidence_bag.ports = context.network_info.get("ports") or []
            evidence_bag.services = context.network_info.get("services") or []

        classification = classify_device(evidence_bag)

        # High confidence for SNMP-backed identification (85-98)
        confidence = max(int(classification.confidence or 0), 85)
        if snmp_info.manufacturer and snmp_info.model:
            confidence = max(confidence, 92)
        if snmp_info.serial_number:
            confidence = max(confidence, 95)

        evidence = compact_identification_evidence(
            os_name=snmp_info.sys_descr,
            vendor=snmp_info.manufacturer or snmp_info.vendor_from_oid,
            hostname=snmp_info.sys_name,
            manufacturer=snmp_info.manufacturer,
            model=snmp_info.model,
            serial_number=snmp_info.serial_number,
            firmware_rev=snmp_info.firmware_rev,
            software_rev=snmp_info.software_rev,
            sys_descr=snmp_info.sys_descr,
            sys_object_id=snmp_info.sys_object_id,
            sys_name=snmp_info.sys_name,
            sys_uptime=snmp_info.sys_uptime,
            signals=["snmp"],
        )

        classification.identification_method = self.method_name
        classification.identification_evidence = evidence

        logger.info(
            "[SNMP] Identification successful | host=%s | type=%s "
            "vendor=%s model=%s",
            ip_address,
            classification.device_type,
            snmp_info.manufacturer or "(empty)",
            snmp_info.model or "(empty)",
        )

        return IdentificationResult(
            success=True,
            method=self.method_name,
            device_type=classification.device_type,
            confidence=confidence,
            evidence=evidence,
            metadata={
                "implemented": True,
                "source": "snmp",
                "canonicalType": canonical_device_type(classification.device_type),
                "hostname": snmp_info.sys_name,
            },
            classification=classification,
        )


class SSHIdentifier(FutureStubIdentifier):
    method_name = "ssh"


class CameraIdentifier(BaseIdentifier):
    """
    ONVIF/HTTP-based identification for IP cameras.

    Queries ONVIF DeviceManagement SOAP services (/onvif/device_service) for
    manufacturer, model, firmware version, serial number, hardware ID, and device name.

    Only runs when the device presents camera evidence (preferred_device_type CAMERA,
    RTSP port 554, ONVIF ports/services, camera vendor/OUI, camera hostname hints,
    or configured ONVIF credentials).

    On any failure, the IdentificationManager automatically falls through
    to NmapIdentifier.
    """

    method_name = "onvif"

    def supports(self, context: IdentificationContext) -> bool:
        """
        True when:
        1. Device record has credentials.onvifUsername / onvifPassword configured, OR
        2. Preferred or existing device type is CAMERA / IP Camera, OR
        3. Evidence indicates RTSP (port 554), ONVIF ports (8000, 8080, 8899), or camera services, OR
        4. Camera vendor/OUI (Hikvision, Dahua, Axis, Uniview, Reolink, Foscam, Hanwha, Vivotek, Bosch, Mobotix, Panasonic), OR
        5. Hostname hints (contains cam, camera, nvr, dvr, ipcam).

        IMPORTANT: Port 80 alone does NOT trigger camera identification.
        """
        existing = context.existing or {}
        creds = existing.get("credentials") or {}
        if creds.get("onvifUsername") or creds.get("onvifPassword"):
            return True

        preferred = canonical_device_type(
            context.preferred_device_type
            or (existing.get("identification") or {}).get("displayType")
            or existing.get("deviceType")
            or ""
        )
        if preferred == "CAMERA":
            return True

        net_info = context.network_info or {}
        ports = set()
        for p in net_info.get("ports") or []:
            if str(p.get("state") or "").lower() == "open":
                try:
                    ports.add(int(p.get("port") or 0))
                except (TypeError, ValueError):
                    pass

        services = {str(s).lower() for s in (net_info.get("services") or []) if s}

        # RTSP port 554 or ONVIF ports (8000, 8080, 8899)
        if 554 in ports or bool(ports & {8000, 8080, 8899}):
            return True
        if bool(services & {"rtsp", "onvif", "camera", "ipcam"}):
            return True

        # Camera vendor/OUI matching
        vendor = (
            net_info.get("vendor")
            or existing.get("vendor")
            or ""
        ).lower()
        camera_vendors = (
            "hikvision", "dahua", "axis", "uniview", "reolink", "foscam",
            "hanwha", "vivotek", "bosch", "mobotix", "panasonic",
        )
        if any(v in vendor for v in camera_vendors):
            return True

        # Hostname hints
        hostname = (
            net_info.get("hostname")
            or existing.get("hostname")
            or ""
        ).lower()
        if any(hint in hostname for hint in ("camera", "ipcam", "nvr", "dvr")) or re.search(r"\bcam\b", hostname):
            return True

        return False

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        from services.onvif_service import OnvifCollectorError, query_onvif_device  # noqa: PLC0415
        from utils.secret_crypto import decrypt_secret  # noqa: PLC0415

        ip_address = context.ip_address
        existing = context.existing or {}
        creds = existing.get("credentials") or {}

        username = str(creds.get("onvifUsername") or "").strip()
        encrypted_password = creds.get("onvifPassword") or ""
        port = int(creds.get("onvifPort") or 80)

        password = ""
        if encrypted_password:
            try:
                password = decrypt_secret(encrypted_password) or ""
            except Exception:  # noqa: BLE001
                pass

        try:
            from config.database import ONVIF_TIMEOUT  # noqa: PLC0415

            onvif_info = query_onvif_device(
                ip_address,
                username,
                password,
                port=port,
                timeout=ONVIF_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            for secret in (username, password):
                if secret and isinstance(secret, str) and secret in error_msg:
                    error_msg = error_msg.replace(secret, "***")
            logger.info(
                "[ONVIF] Identification failed | host=%s | error=%s",
                ip_address,
                error_msg[:200],
            )
            return IdentificationResult(
                success=False,
                method=self.method_name,
                device_type=DEVICE_TYPE_UNKNOWN,
                confidence=0,
                error=error_msg[:500],
                metadata={"implemented": True, "source": "onvif"},
            )

        # High confidence for ONVIF-identified IP camera
        confidence = 96

        evidence = compact_identification_evidence(
            vendor=onvif_info.manufacturer,
            hostname=onvif_info.device_name or (context.network_info or {}).get("hostname") or "",
            manufacturer=onvif_info.manufacturer,
            model=onvif_info.model,
            serial_number=onvif_info.serial_number,
            firmware_rev=onvif_info.firmware_version,
            device_name=onvif_info.device_name,
            onvif_hardware_id=onvif_info.hardware_id,
            signals=["onvif"],
        )

        from services.discovery.classifier import (  # noqa: PLC0415
            ClassificationResult,
            DEVICE_TYPE_IP_CAMERA,
        )

        classification = ClassificationResult(
            hostname=onvif_info.device_name or (existing.get("hostname") or ""),
            vendor=onvif_info.manufacturer,
            operating_system=f"ONVIF {onvif_info.firmware_version}".strip(),
            device_type=DEVICE_TYPE_IP_CAMERA,
            confidence=confidence,
            classification_method="onvif-device-management",
            discovery_source="onvif",
            signals_matched=["onvif"],
            canonical_type="CAMERA",
            identification_evidence=evidence,
            identification_method=self.method_name,
        )

        logger.info(
            "[ONVIF] Identification successful | host=%s | mfg=%s model=%s fw=%s name=%s",
            ip_address,
            onvif_info.manufacturer or "(empty)",
            onvif_info.model or "(empty)",
            onvif_info.firmware_version or "(empty)",
            onvif_info.device_name or "(empty)",
        )

        return IdentificationResult(
            success=True,
            method=self.method_name,
            device_type=DEVICE_TYPE_IP_CAMERA,
            confidence=confidence,
            evidence=evidence,
            metadata={
                "implemented": True,
                "source": "onvif",
                "canonicalType": "CAMERA",
                "hostname": onvif_info.device_name,
            },
            classification=classification,
        )


class IdentificationManager:
    """Select an identification plan and execute the first successful result."""

    def __init__(self, identifiers: dict[str, BaseIdentifier] | None = None):
        self.identifiers = identifiers or {
            "nmap": NmapIdentifier(),
            "windows": WindowsIdentifier(),
            "snmp": SNMPIdentifier(),
            "ssh": SSHIdentifier(),
            "camera": CameraIdentifier(),
        }

    def _preferred_type(self, context: IdentificationContext) -> str:
        if context.preferred_device_type:
            return str(context.preferred_device_type)
        existing = context.existing or {}
        identification = existing.get("identification") or {}
        return (
            identification.get("displayType")
            or identification.get("deviceType")
            or existing.get("deviceType")
            or ""
        )

    def plan_methods(self, context: IdentificationContext) -> list[str]:
        preferred = canonical_device_type(self._preferred_type(context))
        if preferred in {"PC", "LAPTOP", "SERVER"}:
            return ["windows", "nmap"]
        if preferred == "PRINTER":
            return ["snmp", "nmap"]
        if preferred == "CAMERA":
            return ["camera", "nmap"]
        if preferred in {"SWITCH", "ROUTER", "FIREWALL", "ACCESS_POINT", "NETWORK_DEVICE"}:
            return ["snmp", "ssh", "nmap"]

        # Inspect network_info evidence for open ports / services / vendor / OS
        net_info = context.network_info or {}
        ports = set()
        for p in net_info.get("ports") or []:
            if str(p.get("state") or "").lower() == "open":
                try:
                    ports.add(int(p.get("port") or 0))
                except (TypeError, ValueError):
                    pass
        services = {str(s).lower() for s in (net_info.get("services") or []) if s}
        vendor = str(net_info.get("vendor") or "").lower()
        os_name = str((net_info.get("os") or {}).get("name") or "").lower()

        # Windows PC / Server hints
        if bool(ports & {135, 445, 5985, 5986}) or "smb" in services or "windows" in os_name or "microsoft" in vendor:
            return ["windows", "nmap"]

        # Printer hints
        if bool(ports & {9100, 515, 631}) or bool(services & {"printer", "ipp", "jetdirect"}):
            return ["snmp", "nmap"]

        # Camera hints
        camera_vendors = (
            "hikvision", "dahua", "axis", "uniview", "reolink", "foscam",
            "hanwha", "vivotek", "bosch", "mobotix", "panasonic",
        )
        if bool(ports & {554, 8000, 8080, 8899}) or bool(services & {"rtsp", "onvif", "camera"}) or any(v in vendor for v in camera_vendors):
            return ["camera", "nmap"]

        # Network Device / Switch / Router hints
        if 161 in ports or "snmp" in services:
            return ["snmp", "ssh", "nmap"]

        return ["nmap"]

    def identify(self, context: IdentificationContext) -> IdentificationResult:
        attempted: list[str] = []
        last_failure: IdentificationResult | None = None
        plan = self.plan_methods(context)

        for method in plan:
            identifier = self.identifiers.get(method)
            if identifier is None or not identifier.supports(context):
                continue
            attempted.append(method)
            result = identifier.identify(context)
            result.metadata = {
                **dict(result.metadata or {}),
                "attemptedMethods": list(attempted),
                "plannedMethods": list(plan),
            }
            if result.success:
                return result
            last_failure = result

        return last_failure or IdentificationResult(
            success=False,
            method="none",
            device_type=DEVICE_TYPE_UNKNOWN,
            confidence=0,
            error="No identifier was available",
            metadata={
                "implemented": False,
                "attemptedMethods": attempted,
                "plannedMethods": list(plan),
                "canonicalType": CANONICAL_UNKNOWN,
            },
        )


DEFAULT_IDENTIFICATION_MANAGER = IdentificationManager()
