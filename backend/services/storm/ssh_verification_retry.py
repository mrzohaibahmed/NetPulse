"""
Bounded retry for read-only post-command SSH verification.

Used by mitigation and recovery verifiers after configuration commands
have fully completed.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.ssh_verification")

MAX_VERIFICATION_ATTEMPTS = 3
VERIFICATION_RETRY_DELAY_SECONDS = 0.5


def verify_with_bounded_retry(
    *,
    label: str,
    attempt_fn: Callable[[], tuple[bool, str]],
) -> tuple[bool, str]:
    """
    Retry read-only verification when state is not yet confirmed.

    ``attempt_fn`` must perform a single verification read and return
    ``(confirmed, raw_output)`` without executing configuration commands.
    """
    last_output = ""
    for attempt in range(1, MAX_VERIFICATION_ATTEMPTS + 1):
        confirmed, output = attempt_fn()
        last_output = output
        if confirmed:
            if attempt > 1:
                logger.info(
                    "Interface verification confirmed | label=%s | attempt=%s/%s",
                    label,
                    attempt,
                    MAX_VERIFICATION_ATTEMPTS,
                )
            return True, output

        if attempt < MAX_VERIFICATION_ATTEMPTS:
            logger.info(
                "Interface verification not yet confirmed; retrying | label=%s | attempt=%s/%s",
                label,
                attempt,
                MAX_VERIFICATION_ATTEMPTS,
            )
            time.sleep(VERIFICATION_RETRY_DELAY_SECONDS)

    logger.warning(
        "Interface verification failed after %s attempts | label=%s",
        MAX_VERIFICATION_ATTEMPTS,
        label,
    )
    return False, last_output
