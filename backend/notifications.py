from datetime import datetime, timedelta
import os
from pathlib import Path
import requests
from google.auth.transport.requests import Request
import firebase_admin
from firebase_admin import credentials, messaging
import json
import subprocess
import tempfile

from database import (
    add_device_token,
    list_device_tokens,
    list_unmuted_device_tokens,
    remove_device_token,
    add_notification_history,
    list_notification_history,
    set_device_muted_until,
    get_device_muted_until,
)

SERVICE_ACCOUNT_PATH = Path(os.environ.get("SURVCAM_FIREBASE_CREDENTIALS", Path(__file__).resolve().parent / "firebase-service-account.json"))
MUTE_DURATION_MINUTES = 60
IGNORED_CAMERA_NOTIFICATION_IDS = {item.strip() for item in os.environ.get("SURVCAM_IGNORED_CAMERA_NOTIFICATION_IDS", "").split(",") if item.strip()}

_firebase_app = None


def init_firebase():
    global _firebase_app

    if _firebase_app is not None:
        return

    if not SERVICE_ACCOUNT_PATH.exists():
        print(f"WARNING: Firebase service account not found at {SERVICE_ACCOUNT_PATH}. "
              f"Push notifications are disabled until this file is added.")
        return

    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    _firebase_app = firebase_admin.initialize_app(cred, {
        "httpTimeout": 10,
    })
    print("Firebase initialized - push notifications enabled")


def register_token(token: str):
    add_device_token(token, platform="android")


def unregister_token(token: str):
    remove_device_token(token)


def is_device_muted(token: str) -> bool:
    muted_until = get_device_muted_until(token)

    if not muted_until:
        return False

    try:
        return datetime.fromisoformat(muted_until) > datetime.now()
    except ValueError:
        return False


def mute_device_for_one_hour(token: str):
    until = datetime.now() + timedelta(minutes=MUTE_DURATION_MINUTES)
    set_device_muted_until(token, until.isoformat())
    return until.isoformat()

def mute_device_for_hours(token: str, hours: int = 1):
    until = datetime.now() + timedelta(hours=hours)
    set_device_muted_until(token, until.isoformat())
    return until.isoformat()


def unmute_device(token: str):
    set_device_muted_until(token, None)


def get_device_mute_status(token: str):
    muted_until = get_device_muted_until(token)
    muted = is_device_muted(token)

    return {
        "muted": muted,
        "muted_until": muted_until if muted else None,
    }


def send_notification(
    notif_type: str,
    title: str,
    body: str,
    camera_id: str | None = None,
    data: dict | None = None,
    data_only: bool = False,
):
    """Sends a push to every registered device (unless muted), and always
    logs to notification_history regardless of mute state or whether the
    push itself succeeded - so the in-app history stays a complete record
    of what happened, even during a muted window.

    data_only=True sends a pure data message (no `notification` block).
    This is required for notifications that need a custom action button
    (like motion alerts' "Mute for 1 hour" button): a data-only message
    always reaches the app's onMessageReceived, even in the background,
    letting the client build the notification itself with actions. A
    normal notification+data hybrid is display-only when backgrounded and
    can't carry custom action buttons.
    """
    if camera_id in IGNORED_CAMERA_NOTIFICATION_IDS:
        print(f"Notification ignored for camera {camera_id}: {title}")
        return

    add_notification_history(notif_type, title, body, camera_id)

    if _firebase_app is None:
        print(f"Notification not sent (Firebase not configured): {title} - {body}")
        return

    all_tokens = list_device_tokens()
    tokens = list_unmuted_device_tokens()
    muted_count = len(all_tokens) - len(tokens)

    if not tokens:
        print(f"Notification not sent (no unmuted registered devices): {title}")
        return

    if muted_count:
        print(f"Notification skipped for {muted_count} muted device(s): {title}")

    payload_data = {"type": notif_type, "title": title, "body": body}
    if camera_id:
        payload_data["camera_id"] = camera_id
    if data:
        payload_data.update({k: str(v) for k, v in data.items()})

    if data_only:
        message = messaging.MulticastMessage(
            data=payload_data,
            tokens=tokens,
        )
    else:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=payload_data,
            tokens=tokens,
        )

    try:
        credential = _firebase_app.credential.get_credential().with_scopes([
            "https://www.googleapis.com/auth/firebase.messaging"
        ])
        credential.refresh(Request())

        project_id = credential.project_id
        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

        success_count = 0

        for token in tokens:
            token_preview = token[:18] + "..."

            message_payload = {
                "message": {
                    "token": token,
                    "data": payload_data,
                    "android": {
                        "priority": "HIGH",
                    },
                }
            }

            if not data_only:
                message_payload["message"]["notification"] = {
                    "title": title,
                    "body": body,
                }

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as payload_file:
                json.dump(message_payload, payload_file)
                payload_path = payload_file.name

            try:
                result = subprocess.run(
                    [
                        "curl",
                        "--silent",
                        "--show-error",
                        "--max-time",
                        "30",
                        "--write-out",
                        "\nHTTP_STATUS:%{http_code}",
                        url,
                        "-H",
                        f"Authorization: Bearer {credential.token}",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        f"@{payload_path}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=35,
                )

                output = result.stdout or ""
                error_output = result.stderr or ""

                if "HTTP_STATUS:" in output:
                    body_text, status_text = output.rsplit("HTTP_STATUS:", 1)
                    status_code = int(status_text.strip())
                else:
                    body_text = output
                    status_code = 0

                if result.returncode == 0 and status_code == 200:
                    success_count += 1
                    print(f"FCM sent to {token_preview}: {body_text[:200]}")
                else:
                    print(
                        f"FCM failed for {token_preview}: "
                        f"curl_exit={result.returncode}, status={status_code}, "
                        f"body={body_text[:1000]}, stderr={error_output[:500]}"
                    )

                    if status_code == 404 or "UNREGISTERED" in body_text or "not registered" in body_text:
                        remove_device_token(token)
                        print(f"Removed invalid FCM token: {token_preview}")
            finally:
                Path(payload_path).unlink(missing_ok=True)

        print(f"Notification result: {title} ({success_count}/{len(tokens)} succeeded)")
    except Exception as exc:
        print(f"Notification send failed: {exc}")


def get_history(limit: int = 100):
    return list_notification_history(limit=limit)