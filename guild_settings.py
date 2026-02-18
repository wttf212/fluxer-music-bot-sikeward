import json
import os

SETTINGS_FILE = os.environ.get(
    "GUILD_SETTINGS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "guild_settings.json")
)


def load_settings() -> dict:
    if not os.path.isfile(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)


def save_settings(data: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_allowed_channel(guild_id: str) -> str | None:
    settings = load_settings()
    guild = settings.get(guild_id, {})
    return guild.get("allowed_channel")


def set_allowed_channel(guild_id: str, channel_id: str):
    settings = load_settings()
    if guild_id not in settings:
        settings[guild_id] = {}
    settings[guild_id]["allowed_channel"] = channel_id
    save_settings(settings)
