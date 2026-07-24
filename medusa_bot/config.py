import json
import os
from datetime import datetime, timezone
from typing import Optional

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
CONFIG_FILE = "config.json"
ADMIN_FEE_RATE = 0.005
LEADERBOARD_API = "https://medusablox.com/api/roblox/external/leaderboard"
ROBLOX_GROUP_IDS = os.getenv("ROBLOX_GROUP_IDS", "")
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY", "")
ROBLOX_USER_LOOKUP_API = "https://users.roblox.com/v1/usernames/users"
ROBLOX_EXTERNAL_ORDER_API = os.getenv(
    "ROBLOX_EXTERNAL_ORDER_API",
    "http://localhost:8000/api/roblox/external/order",
)
ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API = os.getenv(
    "ROBLOX_EXTERNAL_UPLOAD_PAYMENT_API",
    "http://localhost:8000/api/roblox/external/order/upload-payment",
)
MEDUSABLOX_GUILD_ID = 1479845174430404738
MEDUSABLOX_DISCORD_INVITE_URL = "https://discord.gg/BJ6hQE8zAb"
SLASH_SYNC_COOLDOWN_SECONDS = int(os.getenv("SLASH_SYNC_COOLDOWN_SECONDS", "900"))
FORCE_SLASH_SYNC = os.getenv("FORCE_SLASH_SYNC", "0") == "1"
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
ENABLE_MEMBERS_INTENT = os.getenv("ENABLE_MEMBERS_INTENT", "0") == "1"
CALC_RATES = {
    "group": 138000,
    "gamepass": 128000,
    "gig": 115000,
    "send": 143000,
}
CALC_MIN_ROBUX = 125
CALC_TYPE_ORDER = ["group", "gamepass", "gig", "send"]
CALC_TYPE_LABELS = {
    "group": "Instant Group",
    "gamepass": "Gamepass",
    "gig": "Gig",
    "send": "Send",
}
CALC_TYPE_ICONS = {
    "group": "💎",
    "gamepass": "🎫",
    "gig": "🎁",
    "send": "✈️",
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_bot_meta() -> dict:
    return load_config().get("__meta__", {})


def set_bot_meta_value(key: str, value):
    config = load_config()
    meta = config.get("__meta__", {})
    meta[key] = value
    config["__meta__"] = meta
    save_config(config)


def should_sync_slash_commands() -> tuple[bool, int]:
    if FORCE_SLASH_SYNC:
        return True, 0

    last_synced_at = get_bot_meta().get("last_slash_sync_at", 0)
    try:
        last_synced_at = int(last_synced_at)
    except (TypeError, ValueError):
        last_synced_at = 0

    now_ts = int(datetime.now(timezone.utc).timestamp())
    elapsed = now_ts - last_synced_at
    if elapsed >= SLASH_SYNC_COOLDOWN_SECONDS:
        return True, 0

    return False, SLASH_SYNC_COOLDOWN_SECONDS - elapsed


def get_guild_config(guild_id: int) -> Optional[dict]:
    return load_config().get(str(guild_id))


def has_qris_config(guild_id: int) -> bool:
    cfg = get_guild_config(guild_id) or {}
    return bool(cfg.get("static_qris") and cfg.get("merchant_name"))


def set_guild_config(guild_id: int, qris: str, merchant: str):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key]["static_qris"] = qris
    config[guild_key]["merchant_name"] = merchant
    save_config(config)


def delete_guild_config(guild_id: int):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key in config:
        config[guild_key].pop("static_qris", None)
        config[guild_key].pop("merchant_name", None)
        if not config[guild_key]:
            del config[guild_key]
        save_config(config)


def set_leaderboard_config(guild_id: int, channel_id: int, message_id: int = None):
    config = load_config()
    if str(guild_id) not in config:
        config[str(guild_id)] = {}
    config[str(guild_id)]["lb_channel_id"] = channel_id
    if message_id:
        config[str(guild_id)]["lb_message_id"] = message_id
    save_config(config)


def delete_leaderboard_config(guild_id: int):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key in config:
        config[guild_key].pop("lb_channel_id", None)
        config[guild_key].pop("lb_message_id", None)
        if not config[guild_key]:
            del config[guild_key]
        save_config(config)


def get_leaderboard_config(guild_id: int) -> Optional[dict]:
    cfg = load_config().get(str(guild_id), {})
    channel_id = cfg.get("lb_channel_id")
    if not channel_id:
        return None
    return {"channel_id": channel_id, "message_id": cfg.get("lb_message_id")}


def set_rating_log_config(guild_id: int, channel_id: int):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key]["rating_log_channel_id"] = channel_id
    save_config(config)


def delete_rating_log_config(guild_id: int):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key in config:
        config[guild_key].pop("rating_log_channel_id", None)
        if not config[guild_key]:
            del config[guild_key]
        save_config(config)


def get_rating_log_config(guild_id: int) -> Optional[dict]:
    cfg = load_config().get(str(guild_id), {})
    channel_id = cfg.get("rating_log_channel_id")
    if not channel_id:
        return None
    return {"channel_id": channel_id}


def set_order_role_config(guild_id: int, role_ids):
    config = load_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    normalized_role_ids = []
    for role_id in role_ids or []:
        role_id = int(role_id)
        if role_id not in normalized_role_ids:
            normalized_role_ids.append(role_id)
    if not normalized_role_ids:
        config[guild_key].pop("order_role_id", None)
        config[guild_key].pop("order_role_ids", None)
        if not config[guild_key]:
            del config[guild_key]
    else:
        config[guild_key]["order_role_ids"] = normalized_role_ids
        config[guild_key].pop("order_role_id", None)
    save_config(config)


def get_order_role_ids(guild_id: int):
    cfg = load_config().get(str(guild_id), {})
    role_ids = cfg.get("order_role_ids")
    if role_ids:
        return [int(role_id) for role_id in role_ids]
    legacy_role_id = cfg.get("order_role_id")
    if legacy_role_id:
        return [int(legacy_role_id)]
    return []
