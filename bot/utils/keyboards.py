"""
Inline keyboard layouts for the bot.
"""
from typing import List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import EMOJI


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Get the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['chart']} System Status",
                callback_data="cmd_status"
            )
        ],
        [
            InlineKeyboardButton(f"{EMOJI['cpu']} CPU", callback_data="cmd_cpu"),
            InlineKeyboardButton(f"{EMOJI['memory']} Memory", callback_data="cmd_memory"),
        ],
        [
            InlineKeyboardButton(f"{EMOJI['disk']} Disk", callback_data="cmd_disk"),
            InlineKeyboardButton(f"{EMOJI['network']} Network", callback_data="cmd_network"),
        ],
        [
            InlineKeyboardButton(f"{EMOJI['process']} Processes", callback_data="cmd_top"),
            InlineKeyboardButton(f"{EMOJI['temp']} Temperature", callback_data="cmd_temp"),
        ],
        [
            InlineKeyboardButton(f"{EMOJI['clock']} Uptime", callback_data="cmd_uptime"),
            InlineKeyboardButton(f"{EMOJI['services']} Services", callback_data="cmd_services"),
        ],
        [
            InlineKeyboardButton(f"{EMOJI['docker']} Docker", callback_data="cmd_docker"),
        ],
        [
            InlineKeyboardButton(f"{EMOJI['alert']} Alerts", callback_data="cmd_alerts"),
            InlineKeyboardButton(f"{EMOJI['help']} Help", callback_data="cmd_help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_docker_menu_keyboard() -> InlineKeyboardMarkup:
    """Get the Docker submenu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['container']} List Containers",
                callback_data="docker_list"
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['back']} Back to Menu",
                callback_data="menu_main"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_docker_back_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard to go back to Docker menu."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['back']} Back to Docker",
                callback_data="cmd_docker"
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['home']} Main Menu",
                callback_data="menu_main"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Get a simple back to main menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['back']} Back to Menu",
                callback_data="menu_main"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_container_list_keyboard(
    containers: List,
    page: int,
    total_pages: int,
    get_status_emoji
) -> InlineKeyboardMarkup:
    """
    Get keyboard with paginated container list.
    
    Args:
        containers: List of ContainerInfo objects for current page
        page: Current page number (0-indexed)
        total_pages: Total number of pages
        get_status_emoji: Function to get emoji for status
    """
    keyboard = []
    
    # Add container buttons (2 per row for better UX)
    row = []
    for container in containers:
        emoji = get_status_emoji(container.status)
        # Truncate name if too long
        name = container.name[:20] + "..." if len(container.name) > 20 else container.name
        row.append(
            InlineKeyboardButton(
                f"{emoji} {name}",
                callback_data=f"docker_detail|{container.short_id}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:  # Add remaining buttons
        keyboard.append(row)
    
    # Add pagination controls
    pagination_row = []
    if page > 0:
        pagination_row.append(
            InlineKeyboardButton("◀️ Prev", callback_data=f"docker_page|{page - 1}")
        )
    
    pagination_row.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
    )
    
    if page < total_pages - 1:
        pagination_row.append(
            InlineKeyboardButton("Next ▶️", callback_data=f"docker_page|{page + 1}")
        )
    
    keyboard.append(pagination_row)
    
    # Add back button
    keyboard.append([
        InlineKeyboardButton(
            f"{EMOJI['back']} Back to Docker",
            callback_data="cmd_docker"
        )
    ])
    
    return InlineKeyboardMarkup(keyboard)


def get_container_detail_keyboard(container_id: str) -> InlineKeyboardMarkup:
    """Get keyboard for container detail view."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['back']} Back to List",
                callback_data="docker_list|0"
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['docker']} Docker Menu",
                callback_data="cmd_docker"
            ),
            InlineKeyboardButton(
                f"{EMOJI['home']} Main Menu",
                callback_data="menu_main"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)
