from __future__ import annotations

from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def authorization_url(
    *, client_id: str, redirect_uri: str, state: str, nonce: str
) -> str:
    return GOOGLE_AUTHORIZATION_ENDPOINT + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
    )


async def exchange_and_verify(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    expected_nonce: str,
) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        token_payload = response.json()
    encoded_id_token = token_payload.get("id_token")
    if not encoded_id_token:
        raise ValueError("Google did not return an identity token")
    claims = id_token.verify_oauth2_token(
        encoded_id_token,
        google_requests.Request(),
        client_id,
        clock_skew_in_seconds=10,
    )
    if claims.get("nonce") != expected_nonce:
        raise ValueError("Google sign-in nonce did not match")
    if claims.get("email_verified") is not True:
        raise ValueError("Google email is not verified")
    if not claims.get("sub") or not claims.get("email"):
        raise ValueError("Google account details are incomplete")
    return claims
