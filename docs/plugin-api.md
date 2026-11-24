# Plugin API v1

Публичные классы и типы доступны из самостоятельного пакета `lsab_api`.
Локальные блоки могут продолжать использовать совместимые импорты `bot.api`. Пакет `bot.api.presentation`
предоставляет общие функции MarkdownV2, клавиатуру возврата, форматирование
метрик и генератор графиков. Типы Telegram можно использовать напрямую.

## Пример блока

Сохраните класс в `bot/blocks/load.py`, а его манифест в
`bot/blocks/load.plugin.toml`. Укажите `load = true` в таблице `[blocks]`
файла `blocks.toml`, выполните `python main.py --check-blocks` и перезапустите
бота. Появятся команда `/load`, кнопка в меню и раздел в `/status`.

```python
from lsab_api import Block, PluginMetadata


class LoadBlock(Block):
    metadata = PluginMetadata(
        id="load",
        name="CPU Load",
        description="Current CPU load",
        version="1.0.0",
        icon="🖥️",
    )

    def summary(self) -> str:
        metrics = self.context.system.get_cpu_metrics(interval=0.5)
        return f"{metrics.percent:.1f}%"
```

```toml
[plugin]
id = "load"
name = "CPU Load"
description = "Current CPU load"
version = "1.0.0"
icon = "🖥️"
entry_point = "bot.blocks.load:LoadBlock"
default_enabled = false
```

Если модуль объявляет `extra_actions`, `permissions` или `sends_alerts`,
укажите их также в манифесте. Манифест определяет данные для обнаружения,
а загрузчик проверяет совпадение с классом. Старым локальным блокам тоже
требуется манифест. Подробнее см. [Plugin Registry](plugin-registry.md).

## Контракт

- `PluginMetadata` объявляет уникальный идентификатор команды и ключа TOML,
  название в меню, описание, иконку, версию блока и версию API. Идентификатор
  начинается с латинской строчной буквы и содержит до 32 символов из
  `a-z`, `0-9` и `_`. Описание ограничено 256 символами.
- `PluginContext` предоставляет идентификатор блока, логгер, системные метрики,
  уведомления и брокер. `config` содержит настройки из `plugins/<id>.toml`
  и доступен только для чтения на верхнем уровне. `data_dir` содержит путь
  к данным блока. `blocks.toml` хранит
  переключатели включения.
- `summary()` возвращает короткий обычный текст. `status_section()` и
  `render()` можно переопределить для разметки MarkdownV2. `view()` оборачивает
  тело в заголовок блока и возвращает `BlockView` с клавиатурой и
  опциональным изображением. `command()` и `callback()` отправляют представление.
- Для вложенного меню добавьте идентификатор действия в `extra_actions` с
  префиксом `<id>_`. Можно передать значение через `<action>|<value>`.
  Кнопка вызывает `submenu(query, action, value)`. Передайте результат в
  `await self.show_view(query, view)`.
- Для фоновых уведомлений укажите `sends_alerts=True` и верните `Alert`
  из `check_alert()` при наличии события. `poll_alert()` перед этим запускает
  `dependency_issue()`. Он возвращает текст проблемы или `None`. Ядро
  выполняет синхронные проверки и рендеринг в рабочих потоках.
- Хуки `setup()`, `start()` и `stop()` асинхронны и необязательны.
  `stop()` должен освобождать выделенные ресурсы и поддерживать завершение
  после частично выполненного `setup()` или `start()`.
- `await self.execute_privileged(action, **params)` возвращает `BrokerResult`.
  Для синхронного рендеринга или проверки в рабочем потоке используйте
  `execute_privileged_sync(action, **params)`. Персональный клиент сверяет
  действие с `metadata.permissions`. Системные права выдаёт администратор
  через [Privilege Broker](privilege-broker.md). В установке без брокера
  `NullBroker` отклоняет запрос с кодом `BROKER_UNAVAILABLE`.

## Версии и совместимость

`PLUGIN_API_VERSION` хранит версию контракта независимо от версии бота.
Patch исправляет ошибки, minor добавляет обратно совместимые возможности,
major меняет контракт несовместимо. Для API v1 значение
`PluginMetadata.api_version` по умолчанию равно `"1.0"`. Загрузчик допускает версии с той же major-версией и требуемой версией API
до уровня, предоставленного ядром.

Существующие локальные блоки, наследующие `Block` из `bot.blocks` или
`bot.blocks.base`, могут использовать прежние атрибуты
`name`, `title`, `icon`, `description`, `sends_alerts` и конструктор
`(monitor, alerts)`. Для новых блоков используйте `lsab_api.Block`.

Об упаковке и регистрации см. [Внешние плагины](external-plugins.md).
