"""Services a block receives from the bot."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, TypedDict

from bot.api.broker import BrokerResult
from bot.models import Alert, CPUMetrics, DiskMetrics, MemoryMetrics, NetworkMetrics, ProcessInfo
from config import AlertType


class TemperatureReading(Protocol):
    @property
    def label(self) -> str: ...
    @property
    def current(self) -> float: ...
    @property
    def high(self) -> float | None: ...
    @property
    def critical(self) -> float | None: ...


class LoginInfo(TypedDict):
    name: str
    terminal: str
    host: str
    started: datetime


class UptimeInfo(TypedDict):
    boot_time: datetime
    uptime_seconds: float
    days: int
    hours: int
    minutes: int
    users_count: int
    users: list[LoginInfo]


class ServiceStatus(TypedDict):
    name: str
    status: str
    active_state: str
    sub_state: str
    is_running: bool


class SystemProtocol(Protocol):
    """Synchronous host metrics consumed by blocks."""

    def get_cpu_metrics(self, interval: float = 1.0) -> CPUMetrics: ...
    def get_memory_metrics(self) -> MemoryMetrics: ...
    def get_disk_metrics(self, mount_point: str = "/") -> DiskMetrics: ...
    def get_all_disk_metrics(self) -> list[DiskMetrics]: ...
    def get_network_metrics(self, interface: str | None = None) -> list[NetworkMetrics]: ...
    def get_top_processes(self, limit: int = 10) -> list[ProcessInfo]: ...
    def get_temperature(self) -> Mapping[str, Sequence[TemperatureReading]] | None: ...
    def get_uptime_info(self) -> UptimeInfo: ...
    def get_services_status(self, services: list[str] | None = None) -> list[ServiceStatus]: ...


class AlertProtocol(Protocol):
    """Shared alert state and threshold checks."""

    @property
    def cpu_threshold(self) -> int: ...
    @property
    def memory_threshold(self) -> int: ...
    @property
    def disk_threshold(self) -> int: ...

    def check_cpu_alert(self, metrics: CPUMetrics) -> Alert | None: ...
    def check_memory_alert(self, metrics: MemoryMetrics) -> Alert | None: ...
    def check_disk_alert(self, metrics: DiskMetrics) -> Alert | None: ...
    def create_custom_alert(
        self,
        title: str,
        message: str,
        severity: str = "info",
        alert_type: AlertType = AlertType.CUSTOM,
    ) -> Alert: ...


class BrokerProtocol(Protocol):
    """Execute an action scoped to the plugin that owns the context."""

    async def execute(self, action: str, params: Mapping[str, Any]) -> BrokerResult: ...
