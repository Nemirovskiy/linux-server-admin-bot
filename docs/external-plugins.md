# Внешние плагины

Внешний блок устанавливается как отдельный Python-дистрибутив в окружение
бота. Пакет `lsab-api` содержит классы и типы для блока. Публичный API можно
импортировать отдельно от приложения. Прежние импорты `bot.api` в локальных
блоках указывают на те же классы.

В этой ветке приложение использует `lsab-api==1.1.0`. Нативный установщик
и контейнер собирают SDK из `packages/lsab-api`. Перед установкой плагина
используйте файл ограничений, чтобы pip сохранил API, подходящий ядру.

## Подключение

Для управляемых установок и откатов используйте [установщик плагинов](plugin-installer.md).
Для разработки можно установить доверенный пакет в окружение бота через pip.

```bash
.venv/bin/python -m pip install -c packaging/plugin-constraints.txt /path/to/lsab-plugin
.venv/bin/python -m pip install -c packaging/plugin-constraints.txt /path/to/plugin.whl
.venv/bin/python -m pip install -c packaging/plugin-constraints.txt 'git+https://github.com/OWNER/REPOSITORY.git@REVISION'
```

Внешние плагины обнаруживаются выключенными. Используйте CLI, чтобы проверить
совместимость, изменить переключатель и применить конфигурацию.

```bash
lsab plugins list --external
lsab plugins list --failed --json
lsab plugins info hello_system
lsab plugins doctor hello_system
lsab plugins enable hello_system
lsab plugins apply
```

`--builtin` ограничивает список встроенными блоками, `--enabled` показывает
выбранные в конфигурации. `list` и `info` читают установленные метаданные без
импорта модуля. `doctor` может импортировать явно выбранный выключенный блок.
Вручную запущенный бот перечитывает конфигурацию при следующем запуске.

Встроенные идентификаторы зарезервированы. При таком конфликте внешний
претендент отображается в `list --failed` и в поле `rejected` у `info`.
При конфликте двух внешних пакетов оба блокируются. API, Python, платформа
и активные зависимости пакета проверяются до импорта. Ошибки загрузки
и хуков относятся к одному блоку.

## Создание плагина с нуля

1. Скопируйте [шаблон Hello System](../examples/lsab-plugin-hello-system/)
   в отдельный каталог или Git-репозиторий. Одна точка входа на дистрибутив
   позволяет установщику обновлять и удалять блок независимо.
2. Выберите уникальный ID, например `host_report`. Он начинается со строчной
   латинской буквы и содержит до 32 символов из `a-z`, `0-9` и `_`.
   Используйте его как ключ Entry Point и значение `id` в манифесте и классе.
   Имя дистрибутива, например `lsab-plugin-host-report`, и имя Python-пакета
   `lsab_host_report` задаются отдельно.
3. Переименуйте пакет в `src/` и скорректируйте `pyproject.toml`. Добавьте
   зависимость `lsab-api>=1.1,<2`, точку входа группы `lsab.plugins`
   и включите `plugin.toml` в package data.
4. Реализуйте `summary()` как короткую строку без разметки. Для экрана с
   подробностями возвращайте `BlockView` через `self.view(...)`, а динамический
   текст пропускайте через `escape_markdown`. Совместите метаданные и имена
   дополнительных действий класса с манифестом.
5. Получайте настройки и системные сервисы из `self.context`. Подготовку
   ресурсов выполняйте в `setup()`, запуск фоновой работы в `start()`,
   освобождение в `stop()`. Проверка `doctor` создаёт экземпляр и вызывает
   `dependency_issue()`, поэтому этот метод должен работать до `setup()`.
   Записывайте локальное состояние только в предоставленный `data_dir`.
6. Добавьте тесты для `summary()`, экрана, разбора настроек и диагностики
   с подставными сервисами в `PluginContext`. Подготовьте wheel и проверьте
   установку в отдельном тестовом окружении.

Пример модуля `src/lsab_host_report/plugin.py`. Укажите те же значения
`name`, `description`, `icon` и `api_version` в его `plugin.toml`.

```python
from importlib.metadata import version
from platform import node

from lsab_api import Block, BlockView, PluginMetadata, escape_markdown


class HostReportBlock(Block):
    metadata = PluginMetadata(
        id="host_report",
        name="Host Report",
        description="Show this server's name",
        version=version("lsab-plugin-host-report"),
        icon="🧩",
        api_version="1.1",
    )

    def summary(self) -> str:
        return node() if self.context.config.get("show_hostname", True) else "Available"

    def render(self) -> BlockView:
        return self.view(escape_markdown(self.summary()))
```

В `pyproject.toml` укажите
`host_report = "lsab_host_report.plugin:HostReportBlock"` под
`[project.entry-points."lsab.plugins"]`. Из каталога бота с уже установленным
SDK выполните сборку и проверку. Замените путь к своему проекту.

```bash
.venv/bin/python -m pip wheel --no-deps --wheel-dir /tmp/lsab-host-report \
  /path/to/lsab-plugin-host-report
.venv/bin/lsab plugins install /tmp/lsab-host-report/lsab_plugin_host_report-0.1.0-py3-none-any.whl --dry-run
.venv/bin/lsab plugins install /tmp/lsab-host-report/lsab_plugin_host_report-0.1.0-py3-none-any.whl
.venv/bin/lsab plugins list --external
.venv/bin/lsab plugins info host_report
.venv/bin/lsab plugins doctor host_report
.venv/bin/lsab plugins enable host_report
.venv/bin/lsab plugins check
```

Для пользовательской службы примените переключатель через
`.venv/bin/lsab plugins apply`. В ручном режиме запустите или перезапустите
бота через `packaging/run-native.sh`. Проверьте команду `/host_report` в
Telegram. Для следующего релиза измените версию дистрибутива и установите
новый wheel через `lsab plugins update host_report --source /path/to/new.whl`.
Если меняется формат хранимых данных, увеличьте `[data].schema_version`
и проверьте обновление с резервной копией до выпуска.

## Пакет и манифест

Пакет объявляет точку входа с ID блока. Положите `plugin.toml` в корень
своего Python-пакета и включите его в wheel.

```toml
[project]
name = "lsab-plugin-hello-system"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["lsab-api>=1.1,<2"]

[project.entry-points."lsab.plugins"]
hello_system = "lsab_hello_system.plugin:HelloSystemBlock"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
lsab_hello_system = ["plugin.toml"]
```

Настройте манифест с тем же ID и метаданными, которые объявляет класс.

```toml
[plugin]
id = "hello_system"
name = "Hello System"
description = "Server name, OS, and local time"
api_version = "1.1"
icon = "🧩"
default_enabled = false

[permissions]
required = []
optional = []

[compatibility]
platforms = ["linux"]
```

Версия блока берётся из метаданных Python-дистрибутива. Класс может
использовать `importlib.metadata.version("lsab-plugin-hello-system")` для
`PluginMetadata.version`. Поля `extra_actions` и `sends_alerts` при
необходимости указываются в `[plugin]`. Объединённый список `required` и
`optional` должен совпадать с `PluginMetadata.permissions`. Формат
обнаружения опирается на файлы, записанные в метаданных установки.
Обычная установка из каталога включает манифест в установочную запись.
Для разработки также поддерживается `lsab plugins install /path/to/project
--editable` с плоской структурой или каталогом `src`.

Импортируйте `Block`, `BlockView`, `PluginMetadata`, `PluginContext` и
`escape_markdown` из `lsab_api`. Для Telegram-команд ядро выполняет
авторизацию и ограничение частоты перед вызовом `Block.command()`.
Для работы с хостом используйте сервисы из `self.context`.
[Исходники Hello System](../examples/lsab-plugin-hello-system/) можно
использовать как основу своего репозитория.

## Настройки, данные и разрешения

Параметры модуля хранятся в `plugins/<id>.toml` рядом с используемым
`blocks.toml`. Ядро читает таблицу `[settings]` и передаёт её как
`self.context.config`. Плагин проверяет собственные значения и defaults.

```toml
[settings]
show_hostname = true
show_uptime = false
```

Для запущенного внешнего блока ядро предоставляет каталог
`data/plugins/<id>/` рядом с `blocks.toml`. Его путь доступен через
`self.context.data_dir`. После удаления пакета переключатель, настройки
и каталог данных сохраняются. CLI помечает отсутствующий блок как `missing`.

Нативный установщик может зарегистрировать операции из готового wheel
при отдельном одобрении системного администратора. Если обязательные права
недоступны, ядро блокирует загрузку до импорта. Без дополнительного права
блок может запуститься и обработать отказ при выполнении операции.

Плагины работают в процессе бота. Устанавливайте пакеты, которым доверяете
доступ к данным этого процесса. Отключённый переключатель предотвращает
обычный импорт при запуске. Сборка пакета через pip может выполняться
ещё до появления переключателя.

### Операции брокера

В манифесте ID операции начинается с ID плагина. Каждая операция должна быть
указана в `permissions.required` или `permissions.optional`. Например, для
плагина `crowdsec` добавьте следующие таблицы.

```toml
[permissions]
required = ["crowdsec.decisions.list"]
optional = []

[broker.operations."crowdsec.decisions.list"]
argv = ["/usr/bin/cscli", "decisions", "list", "--since", "{hours}h"]
parameters = { hours = { min = 1, max = 168 } }
```

В `PluginMetadata.permissions` укажите тот же ID. Вызов
`await self.execute_privileged("crowdsec.decisions.list", hours=24)`
передаст брокеру целое число, а брокер сформирует аргумент `24h`.
Успешный вызов CLI-операции возвращает стандартный вывод в `BrokerResult.data`.
Используйте абсолютный канонический путь к системному бинарнику.

Для операции Docker укажите `docker_action`, например
`docker.containers.restart`, и `containers = []`. При установке администратор
вводит полные ID контейнеров для этого хоста. Манифест также может предложить
список ID, который администратор вправе заменить. При подтверждении брокер
показывает имена найденных контейнеров вместе с ID. Вызов принимает
`container_id`. Права на пересозданный контейнер с новым ID требуют нового
одобрения.

Системный инструмент показывает утверждаемую команду и границы параметров.
Администратор проверяет, что сама утилита, её аргументы, конфигурация и вывод
подходят для выдачи прав. При общем UID приложения эти права доступны
доверенному коду в процессе бота. Установщик не выдаёт их для `--editable`
и сборок из исходников. Для такой разработки сначала соберите wheel отдельно.

## Docker

Dockerfile основного проекта устанавливает SDK в тот же интерпретатор, что
и бот. Для внешних модулей добавьте их установку в свой производный образ,
например из проверенных wheel с файлом ограничений. При обновлении
пересоберите этот образ. Установка пакета внутри временного контейнера
исчезает при его замене. Настройки и данные блоков расположены в общем
каталоге конфигурации Compose и сохраняются через его bind mount.
