from __future__ import annotations

import html

import httpx


class EmailDeliveryError(RuntimeError):
    pass


def send_password_reset_code(
    *, api_key: str, sender: str, recipient: str, display_name: str, code: str, minutes: int
) -> None:
    """Send a short-lived password-reset code through Resend's HTTPS API."""
    safe_name = html.escape(display_name)
    safe_code = html.escape(code)
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": sender,
                "to": [recipient],
                "subject": "Your Movie Compass password reset code",
                "text": (
                    f"Hi {display_name},\n\nYour Movie Compass password reset code is {code}. "
                    f"It expires in {minutes} minutes. If you did not request this, you can "
                    "ignore this email.\n"
                ),
                "html": (
                    f"<p>Hi {safe_name},</p><p>Your Movie Compass password reset code is "
                    f"<strong style=\"font-size:24px;letter-spacing:4px\">{safe_code}</strong>.</p>"
                    f"<p>It expires in {minutes} minutes. If you did not request this, you can "
                    "ignore this email.</p>"
                ),
            },
            timeout=10.0,
        )
    except httpx.HTTPError as error:
        raise EmailDeliveryError("Email provider could not be reached") from error
    if response.status_code >= 400:
        raise EmailDeliveryError(f"Email provider returned HTTP {response.status_code}")
