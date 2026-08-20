"""
Device discovery classification package.

Reusable hostname detection and device-type classification for Nmap-based
scans, network discovery, and device rescans.
"""

from services.discovery.classifier import (
    ClassificationEvidence,
    ClassificationResult,
    classify_device,
    evidence_from_network_info,
)
from services.discovery.apply import (
    apply_classification_to_device,
    enrich_online_host,
)
from services.discovery.enrichment import (
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_ENRICHING,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_PENDING,
    enqueue_discovery_enrichment,
)

__all__ = [
    "ClassificationEvidence",
    "ClassificationResult",
    "classify_device",
    "evidence_from_network_info",
    "apply_classification_to_device",
    "enrich_online_host",
    "DISCOVERY_STATUS_PENDING",
    "DISCOVERY_STATUS_ENRICHING",
    "DISCOVERY_STATUS_COMPLETED",
    "DISCOVERY_STATUS_FAILED",
    "enqueue_discovery_enrichment",
]
