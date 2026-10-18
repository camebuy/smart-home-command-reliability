"""Executable reference flow for a gateway adapter (no real gateway credentials).

The gateway owns device I/O. This service owns command decisions and an in-memory
pending queue; production should replace the queue with durable storage.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Protocol

from policy import should_probe


@dataclass(frozen=True)
class Command:
    command_id: str
    device_id: str
    action: str
    target_state: str
    dispatched_at: str
    last_heartbeat_at: str
    expires_at: str
    safe_to_retry: bool


class Gateway(Protocol):
    def probe(self, device_id: str) -> Optional[bool]: ...
    def dispatch(self, command: Command) -> bool: ...
    def state_matches(self, command: Command) -> Optional[bool]: ...
    def notify(self, command: Command, message: str) -> None: ...
    def escalate(self, command: Command, reason: str) -> None: ...


class InterventionService:
    """Preserve a requested action while limiting duplicate execution risk."""

    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.pending: Dict[str, Command] = {}
        self.completed = set()
        self.unconfirmed = set()

    def submit(self, command: Command) -> str:
        if command.command_id in self.completed:
            return "completed"
        if command.command_id in self.unconfirmed:
            return "unconfirmed"
        if command.command_id in self.pending:
            return "queued"
        if should_probe(command.dispatched_at, command.last_heartbeat_at):
            if self.gateway.probe(command.device_id) is not True:
                self.pending[command.command_id] = command
                self.gateway.notify(command, "设备暂时离线，指令已保留，恢复后再尝试")
                return "queued"
        return self._send(command)

    def on_reconnected(self, device_id: str, now: str) -> List[str]:
        """Called by a real gateway's reconnect event or scheduled recovery worker."""
        results = []
        for command in list(self.pending.values()):
            if command.device_id != device_id:
                continue
            if _timestamp(now) >= _timestamp(command.expires_at):
                del self.pending[command.command_id]
                self.gateway.notify(command, "等待超时，指令未执行，请手动确认")
                self.gateway.escalate(command, "queued command expired")
                results.append("expired")
            elif self.gateway.probe(device_id) is True:
                del self.pending[command.command_id]
                results.append(self._send(command))
        return results

    def _send(self, command: Command) -> str:
        # A missing acknowledgement does not prove that the device did nothing.
        if self.gateway.dispatch(command):
            return self._success(command)
        observed = self.gateway.state_matches(command)
        if observed is True:
            return self._success(command)
        if observed is False and command.safe_to_retry:
            # One extra send at most, reusing command_id for gateway deduplication.
            if self.gateway.dispatch(command):
                return self._success(command)
            if self.gateway.state_matches(command) is True:
                return self._success(command)
        self.gateway.notify(command, "执行结果未确认，请勿重复操作，已转人工排查")
        self.gateway.escalate(command, "execution unconfirmed")
        self.unconfirmed.add(command.command_id)
        return "unconfirmed"

    def _success(self, command: Command) -> str:
        self.completed.add(command.command_id)
        self.gateway.notify(command, "指令已执行")
        return "completed"


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include UTC offset")
    return parsed
