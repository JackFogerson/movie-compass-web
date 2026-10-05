from __future__ import annotations

import html

import httpx


class EmailDeliveryError(RuntimeError):
    pass


def _send_code(
    *,
    api_key: str,
    sender: str,
    recipient: str,
    display_name: str,
    code: str,
    minutes: int,
    subject: str,
    purpose: str,
) -> None:
    safe_name = html.escape(display_name)
    safe_code = html.escape(code)
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "text": (
                    f"Hi {display_name},\n\nYour Movie Compass {purpose} code is {code}. "
                    f"It expires in {minutes} minutes. If you did not request this, you can "
                    "ignore this email.\n"
                ),
                "html": (
                    f"<p>Hi {safe_name},</p><p>Your Movie Compass {html.escape(purpose)} code is "
                    f'<strong style="font-size:24px;letter-spacing:4px">{safe_code}</strong>.</p>'
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


def send_password_reset_code(
    *, api_key: str, sender: str, recipient: str, display_name: str, code: str, minutes: int
) -> None:
    """Send a short-lived password-reset code through Resend's HTTPS API."""
    _send_code(
        api_key=api_key,
        sender=sender,
        recipient=recipient,
        display_name=display_name,
        code=code,
        minutes=minutes,
        subject="Your Movie Compass password reset code",
        purpose="password reset",
    )


def send_email_verification_code(
    *, api_key: str, sender: str, recipient: str, display_name: str, code: str, minutes: int
) -> None:
    """Send a short-lived registration verification code through Resend."""
    _send_code(
        api_key=api_key,
        sender=sender,
        recipient=recipient,
        display_name=display_name,
        code=code,
        minutes=minutes,
        subject="Verify your Movie Compass email",
        purpose="email verification",
    )
