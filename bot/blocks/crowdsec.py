"""Read CrowdSec detections and decisions through cscli."""

import asyncio
import json
import os
import subprocess
from collections import Counter
from datetime import UTC, datetime

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.api import Alert, Block, BlockView, PluginContext, PluginMetadata
from bot.api.presentation import escape_markdown


class CrowdSecBlock(Block):
    metadata = PluginMetadata(
        id="crowdsec",
        name="CrowdSec",
        description="Статус защиты и сводка CrowdSec",
        version="1.0.0",
        icon="🛡️",
        sends_alerts=True,
        permissions=("crowdsec.alerts.read", "crowdsec.decisions.read"),
    )
    extra_actions = ("crowdsec_details",)

    def __init__(self, context: PluginContext) -> None:
        super().__init__(context)
        self._use_sudo = os.environ.get("CROWDSEC_USE_SUDO") == "1"
        self._binary = os.environ.get("CROWDSEC_CSCLI", "cscli")
        self._started_at = datetime.now(UTC).replace(microsecond=0)
        self._last_id = 0

    def _run(self, *args: str) -> str:
        command = [self._binary, *args]
        if self._use_sudo:
            command = ["sudo", "-n", "--", *command]
        try:
            result = subprocess.run(
                args=command,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            if self._use_sudo:
                raise RuntimeError("CROWDSEC_USE_SUDO=1, но sudo не найден") from exc
            raise RuntimeError("Установите CrowdSec с cscli или укажите CROWDSEC_CSCLI") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            if self._use_sudo:
                raise RuntimeError(
                    "cscli через sudo -n не выполнен. Проверьте путь к cscli, "
                    "правило NOPASSWD и доступность CrowdSec"
                ) from exc
            raise RuntimeError(
                "команда cscli не выполнена. Проверьте доступ и настройки CrowdSec"
            ) from exc
        return result.stdout

    def _list(self, *args: str) -> list[dict]:
        try:
            records = json.loads(self._run(*args, "-o", "json"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("cscli вернул некорректный JSON") from exc
        if records is None:
            return []
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise RuntimeError("неизвестный формат ответа cscli")
        return records

    def _events(self) -> list[dict]:
        return self._list("alerts", "list", "--since", "24h", "--limit", "0")

    @staticmethod
    def _scenarios(events: list[dict]) -> Counter:
        return Counter(event.get("scenario") or "Неизвестный сценарий" for event in events)

    @staticmethod
    def _banned_ips(events: list[dict]) -> set[str]:
        return {
            decision["value"]
            for event in events
            for decision in event.get("decisions") or []
            if (decision.get("type") or "").lower() == "ban"
            and (decision.get("scope") or "").lower() == "ip"
            and decision.get("value")
        }

    def dependency_issue(self) -> str | None:
        try:
            self._run("lapi", "status")
        except RuntimeError as exc:
            return str(exc)
        return None

    def _protection_status(self) -> str:
        try:
            self._run("lapi", "status")
        except RuntimeError as exc:
            return f"LAPI недоступен ({exc})"
        try:
            bouncers = self._list("bouncers", "list")
        except RuntimeError:
            return "LAPI доступен · статус компонентов блокировки неизвестен"
        if not bouncers:
            return "LAPI доступен · компоненты блокировки не зарегистрированы"
        return f"LAPI доступен · зарегистрировано компонентов блокировки: {len(bouncers)}"

    def summary(self) -> str:
        try:
            events = self._events()
        except RuntimeError as exc:
            return f"Сводка недоступна: {exc}"
        return f"{len(events)} алертов за 24 ч · {len(self._banned_ips(events))} IP с решением ban"

    def render(self) -> BlockView:
        lines = [self._protection_status(), "", "Сводка за 24 часа"]
        try:
            events = self._events()
        except RuntimeError as exc:
            lines.append(f"Данные недоступны: {exc}")
            return self.view(escape_markdown("\n".join(lines)))

        lines.extend(
            [f"Алертов: {len(events)}", f"IP с решением ban: {len(self._banned_ips(events))}"]
        )
        keyboard = None
        if events:
            lines.append("Топ сценариев:")
            lines.extend(
                f"• {name[:100]}: {count}" for name, count in self._scenarios(events).most_common(3)
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Подробнее", callback_data="crowdsec_details")],
                    [InlineKeyboardButton("← К блокам", callback_data="menu_main")],
                ]
            )
        return self.view(escape_markdown("\n".join(lines)), keyboard=keyboard)

    def _details(self) -> BlockView:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("← Основное", callback_data="cmd_crowdsec")],
                [InlineKeyboardButton("← К блокам", callback_data="menu_main")],
            ]
        )
        try:
            events = self._events()
        except RuntimeError as exc:
            return self.view(escape_markdown(f"Данные недоступны: {exc}"), keyboard=keyboard)

        scenarios = self._scenarios(events)
        lines = [
            "Подробнее за 24 часа",
            f"Всего алертов: {len(events)}",
            f"IP с решением ban: {len(self._banned_ips(events))}",
            f"Сценарии: {len(scenarios)}",
        ]
        lines.extend(f"• {name[:100]}: {count}" for name, count in scenarios.most_common(10))
        if events:
            lines.extend(["", "Последние алерты:"])
            latest = sorted(events, key=lambda event: event.get("id") or 0, reverse=True)[:8]
            for event in latest:
                name = event.get("scenario") or "Неизвестный сценарий"
                when = (event.get("created_at") or "")[:19].replace("T", " ")
                count = event.get("events_count") or 0
                lines.append(f"• {when} · {name[:72]} · событий: {count}")
        return self.view(escape_markdown("\n".join(lines)), keyboard=keyboard)

    async def submenu(self, query: CallbackQuery, action: str, value: str) -> None:
        await self.show_view(query, await asyncio.to_thread(self._details))

    def check_alert(self) -> Alert | None:
        events = self._events()
        fresh = []
        latest_id = self._last_id
        for event in events:
            event_id = event.get("id")
            if not isinstance(event_id, int) or event_id <= self._last_id:
                continue
            latest_id = max(latest_id, event_id)
            try:
                created = datetime.fromisoformat(event.get("created_at") or "")
                if created.tzinfo is None or created < self._started_at:
                    continue
            except ValueError:
                self.context.logger.warning("CrowdSec alert %s has an invalid timestamp", event_id)
                continue
            source_kind = event.get("kind") or ""
            if (
                not event.get("scenario")
                or event.get("simulated")
                or source_kind
                not in {
                    "",
                    "crowdsec",
                    "waf",
                    "bot-detection",
                }
            ):
                continue
            origins = {decision.get("origin") for decision in event.get("decisions") or []}
            if not source_kind and origins and origins.isdisjoint({"crowdsec", None, ""}):
                continue
            fresh.append(event)
        self._last_id = latest_id
        if not fresh:
            return None
        lines = [
            f"Новых алертов: {len(fresh)}",
            f"IP с решением ban: {len(self._banned_ips(fresh))}",
        ]
        lines.extend(
            f"• {name[:100]}: {count}" for name, count in self._scenarios(fresh).most_common(5)
        )
        return self.context.alerts.create_custom_alert(
            "CrowdSec: обнаружены атаки", "\n".join(lines), severity="warning"
        )
