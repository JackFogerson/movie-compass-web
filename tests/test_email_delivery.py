import httpx
import pytest
import respx
from app.services.email_delivery import (
    EmailDeliveryError,
    send_email_verification_code,
    send_password_reset_code,
)


@respx.mock
def test_password_reset_email_uses_resend_api_without_exposing_key() -> None:
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "email-1"})
    )

    send_password_reset_code(
        api_key="secret-resend-key",
        sender="Movie Compass <noreply@example.com>",
        recipient="fan@example.com",
        display_name="Movie Fan",
        code="123456",
        minutes=15,
    )

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer secret-resend-key"
    assert b'"to":["fan@example.com"]' in request.content
    assert b"123456" in request.content


@respx.mock
def test_password_reset_email_reports_provider_failure() -> None:
    respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(403, json={"message": "sender not verified"})
    )

    with pytest.raises(EmailDeliveryError, match="HTTP 403"):
        send_password_reset_code(
            api_key="secret-resend-key",
            sender="Movie Compass <noreply@example.com>",
            recipient="fan@example.com",
            display_name="Movie Fan",
            code="123456",
            minutes=15,
        )


@respx.mock
def test_registration_verification_email_has_distinct_purpose() -> None:
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "email-2"})
    )

    send_email_verification_code(
        api_key="secret-resend-key",
        sender="Movie Compass <noreply@example.com>",
        recipient="new@example.com",
        display_name="New Viewer",
        code="654321",
        minutes=15,
    )

    request = route.calls[0].request
    assert b"Verify your Movie Compass email" in request.content
    assert b"email verification" in request.content
    assert b"654321" in request.content
