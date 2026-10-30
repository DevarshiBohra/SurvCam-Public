import os
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TELEGRAM_API_URL = "https://api.telegram.org/bot{}/sendMessage"


def send_login_code(username: str, code: str, expires_minutes: int):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        raise RuntimeError("Telegram 2FA is not configured")

    message = (
        "SurvCam login code\n\n"
        f"User: {username}\n"
        f"Code: {code}\n"
        f"Expires in: {expires_minutes} minutes"
    )
    body = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(
        TELEGRAM_API_URL.format(bot_token),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            if response.status != 200 or not result.get("ok"):
                raise RuntimeError("Telegram rejected the login code")
    except (HTTPError, URLError) as exc:
        raise RuntimeError("Unable to deliver the login code through Telegram") from exc
