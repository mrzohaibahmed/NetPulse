"""
Mitigation verification logic.
Runs verification commands and evaluates outputs.
"""

from __future__ import annotations

from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.strategy import MitigationStrategy
from services.storm.ssh_verification_retry import verify_with_bounded_retry
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.mitigation.verifier")


def verify_mitigation(
    executor: SSHMitigationExecutor,
    strategy: MitigationStrategy,
    interface: str,
) -> tuple[bool, str]:
    """
    Run verification commands and check if the strategy succeeded.

    Returns
    -------
    (bool, str)
        A tuple of (verification_passed, raw_verification_output)
    """
    vendor = executor.creds.vendor
    commands = strategy.get_verification_commands(interface, vendor)
    logger.info(
        "Running verification commands for strategy=%s | host=%s | commands=%s",
        strategy.name,
        executor.creds.host,
        commands,
    )

    def _single_attempt() -> tuple[bool, str]:
        try:
            collector = executor.collector
            if collector is not None:
                collector.ensure_exec_prompt(settle_seconds=0.0)
            outputs = executor.execute_commands(commands, interface)
            output_text = "\n".join(outputs)
            success = strategy.verify_output(output_text, vendor)
            return success, output_text
        except Exception as exc:
            logger.error(
                "Verification execution failed | strategy=%s | host=%s | error=%s",
                strategy.name,
                executor.creds.host,
                exc,
            )
            return False, f"Verification failed to run: {exc}"

    return verify_with_bounded_retry(
        label=f"mitigation:{strategy.name}",
        attempt_fn=_single_attempt,
    )
