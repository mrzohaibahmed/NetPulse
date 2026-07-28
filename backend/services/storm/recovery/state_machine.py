"""
State machine for storm recovery lifecycle.
Controls state transitions based on cooldown, validation, execution, and recurrence checks.
"""

from __future__ import annotations


class RecoveryState:
    MITIGATED = "MITIGATED"
    WAITING = "WAITING"
    RECHECK = "RECHECK"
    READY_FOR_RECOVERY = "READY_FOR_RECOVERY"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"
    MONITORING = "MONITORING"
    REMITIGATE = "REMITIGATE"


def get_next_state(
    current_state: str,
    *,
    cooldown_expired: bool = False,
    policy_passed: bool = False,
    recovery_started: bool = False,
    verification_passed: bool = False,
    stabilization_complete: bool = False,
    storm_reappeared: bool = False,
) -> str:
    """
    State machine transition logic.

    Transitions
    -----------
    - MITIGATED -> WAITING (always, once cooldown starts tracking)
    - WAITING -> RECHECK (once cooldown expires)
    - RECHECK -> READY_FOR_RECOVERY (if recovery policy passes)
    - RECHECK -> WAITING (if recovery policy fails, resets to cooldown)
    - READY_FOR_RECOVERY -> RECOVERING (when execution is triggered)
    - RECOVERING -> MONITORING (if action succeeds and verified operational)
    - MONITORING -> RECOVERED (if stabilization period completes cleanly)
    - MONITORING -> REMITIGATE (if storm is re-detected during stabilization)
    - RECOVERED -> REMITIGATE (if storm is re-detected after recovery completes)
    """
    state = (current_state or RecoveryState.MITIGATED).upper()

    if state == RecoveryState.MITIGATED:
        return RecoveryState.WAITING

    if state == RecoveryState.WAITING:
        if cooldown_expired:
            return RecoveryState.RECHECK
        return RecoveryState.WAITING

    if state == RecoveryState.RECHECK:
        if policy_passed:
            return RecoveryState.READY_FOR_RECOVERY
        return RecoveryState.WAITING  # Reset cooldown check cycle

    if state == RecoveryState.READY_FOR_RECOVERY:
        if recovery_started:
            return RecoveryState.RECOVERING
        return RecoveryState.READY_FOR_RECOVERY

    if state == RecoveryState.RECOVERING:
        if verification_passed:
            return RecoveryState.MONITORING
        # If verification fails, retry logic is handled at the engine level
        return RecoveryState.RECOVERING

    if state == RecoveryState.MONITORING:
        if storm_reappeared:
            return RecoveryState.REMITIGATE
        if stabilization_complete:
            return RecoveryState.RECOVERED
        return RecoveryState.MONITORING

    if state == RecoveryState.RECOVERED:
        if storm_reappeared:
            return RecoveryState.REMITIGATE
        return RecoveryState.RECOVERED

    return state
