"""
Mitigation rollback implementation.
Executes the rollback commands of a strategy, reconnecting if necessary.
"""

from __future__ import annotations

from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.strategy import MitigationStrategy
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.mitigation.rollback")


def execute_rollback(
    device: dict,
    strategy: MitigationStrategy,
    interface: str,
    executor: SSHMitigationExecutor | None = None,
) -> tuple[bool, list[str]]:
    """
    Revert configuration changes using strategy rollback commands.

    Reuses existing SSH executor if active, otherwise attempts a fresh connection.

    Returns
    -------
    (bool, list[str])
        A tuple of (rollback_succeeded, commands_run)
    """
    owned = False
    if executor is None:
        executor = SSHMitigationExecutor(device)
        owned = True

    vendor = executor.creds.vendor
    commands = strategy.get_rollback_commands(interface, vendor)

    logger.info(
        "Initiating rollback | strategy=%s | host=%s | commands=%s",
        strategy.name,
        executor.creds.host,
        commands,
    )

    try:
        if executor.collector is None:
            logger.info("Rollback: Re-establishing dead SSH connection to %s", executor.creds.host)
            executor.connect()

        executor.execute_commands(commands, interface)
        logger.info("Rollback succeeded | host=%s", executor.creds.host)
        return True, commands
    except Exception as exc:
        logger.error("Rollback failed | host=%s | error=%s", executor.creds.host, exc)
        return False, commands
    finally:
        if owned:
            try:
                executor.close()
            except Exception:
                pass
