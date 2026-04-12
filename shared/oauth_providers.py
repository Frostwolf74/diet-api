from __future__ import annotations

import json
import urllib.parse
import urllib.request

from shared import config


def google_authorize_url(redirect_uri: str, state: str) -> str:
    if not config.GOOGLE_CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID is not configured")
    q = urllib.parse.urlencode(
        {
            "client_id": config.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"


def github_authorize_url(redirect_uri: str, state: str) -> str:
    if not config.GITHUB_CLIENT_ID:
        raise RuntimeError("GITHUB_CLIENT_ID is not configured")
    q = urllib.parse.urlencode(
        {
            "client_id": config.GITHUB_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{q}"


def exchange_google_code(code: str, redirect_uri: str) -> dict:
    if not config.GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_SECRET is not configured")
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        token_body = json.loads(resp.read().decode("utf-8"))
    access = token_body.get("access_token")
    if not access:
        raise ValueError("Google token exchange failed")
    info_req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access}"},
    )
    with urllib.request.urlopen(info_req, timeout=20) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    email = info.get("email") or ""
    name = info.get("name") or email
    if not email:
        raise ValueError("Google did not return an email")
    return {"email": email, "display_name": name}


def exchange_github_code(code: str, redirect_uri: str) -> dict:
    if not config.GITHUB_CLIENT_SECRET:
        raise RuntimeError("GITHUB_CLIENT_SECRET is not configured")
    data = urllib.parse.urlencode(
        {
            "client_id": config.GITHUB_CLIENT_ID,
            "client_secret": config.GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        method="POST",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        token_body = json.loads(resp.read().decode("utf-8"))
    access = token_body.get("access_token")
    if not access:
        raise ValueError("GitHub token exchange failed")
    user_req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(user_req, timeout=20) as resp:
        user = json.loads(resp.read().decode("utf-8"))
    email = user.get("email")
    if not email:
        em_req = urllib.request.Request(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(em_req, timeout=20) as resp:
            emails = json.loads(resp.read().decode("utf-8"))
        primary = next((e for e in emails if e.get("primary")), None)
        email = (primary or {}).get("email") or (emails[0]["email"] if emails else "")
    login = user.get("login") or "github user"
    if not email:
        raise ValueError("GitHub did not return an email (try making email public or granting user:email)")
    return {"email": email, "display_name": login}
