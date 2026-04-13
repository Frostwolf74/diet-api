from __future__ import annotations

import json
import logging
import os
import urllib.parse

import azure.functions as func

from shared import config
from shared.auth_db import (
    decode_token,
    get_user_by_id,
    issue_oauth_state,
    issue_token,
    parse_bearer,
    register_user,
    upsert_oauth_user,
    verify_oauth_state,
    verify_user,
)
from shared.cache import read_insights_cache, write_blob_bytes, write_insights_cache
from shared.data_access import load_diet_dataframe
from shared.oauth_providers import (
    exchange_github_code,
    exchange_google_code,
    github_authorize_url,
    google_authorize_url,
)
from shared.pipeline import (
    build_insights_payload,
    clean_dataframe,
    dataframe_to_csv_bytes,
    load_csv_bytes,
)

app = func.FunctionApp()


def _cors_headers() -> dict[str, str]:
    origin = os.environ.get("CORS_ORIGIN") or "*"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }


def _json(body: dict | list, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body),
        status_code=status,
        mimetype="application/json",
        headers=_cors_headers(),
    )


def _text(body: str, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body,
        status_code=status,
        mimetype="text/plain",
        headers=_cors_headers(),
    )


def _redirect(url: str) -> func.HttpResponse:
    h = _cors_headers()
    h["Location"] = url
    return func.HttpResponse(status_code=302, headers=h)


def _require_user(req: func.HttpRequest) -> dict:
    token = parse_bearer(req)
    if not token:
        raise PermissionError("Unauthorized")
    claims = decode_token(token)
    user = get_user_by_id(claims["sub"])
    if not user:
        raise PermissionError("Unauthorized")
    return user


def _process_diet_csv_bytes(raw: bytes, source_etag: str | None = None) -> None:
    df = clean_dataframe(load_csv_bytes(raw))
    clean_bytes = dataframe_to_csv_bytes(df)
    write_blob_bytes(config.CONTAINER_NAME, config.BLOB_CLEAN, clean_bytes, content_type="text/csv")
    payload = build_insights_payload(df, source_etag=source_etag)
    write_insights_cache(payload)
    logging.info(
        "Diet pipeline complete: %s recipes, insights at %s",
        payload.get("recipe_count"),
        payload.get("generated_at"),
    )


@app.blob_trigger(
    arg_name="myblob",
    path="datasets/All_Diets.csv",
    connection="AzureWebJobsStorage",
)
def on_all_diets_changed(myblob: func.InputStream) -> None:
    """Runs cleaning + precomputed insights when the source blob updates."""
    try:
        data = myblob.read()
        _process_diet_csv_bytes(data, source_etag=getattr(myblob, "name", None))
    except Exception:
        logging.exception("Blob trigger pipeline failed")


@app.route(route="rebuild-cache", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def rebuild_cache(req: func.HttpRequest) -> func.HttpResponse:
    """Manual rebuild (function key) for local dev without blob triggers."""
    try:
        df = load_diet_dataframe()
        if df is None:
            return _json({"error": "No dataset available (blob or local All_Diets.csv)"}, 404)
        clean_bytes = dataframe_to_csv_bytes(df)
        write_blob_bytes(config.CONTAINER_NAME, config.BLOB_CLEAN, clean_bytes, content_type="text/csv")
        payload = build_insights_payload(df, source_etag="manual-rebuild")
        write_insights_cache(payload)
        return _json({"ok": True, "recipe_count": payload.get("recipe_count")})
    except Exception as e:
        logging.exception("rebuild-cache failed")
        return _json({"error": str(e)}, 500)


@app.route(route="auth/register", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def auth_register(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        body = req.get_json()
    except ValueError:
        return _json({"error": "Invalid JSON"}, 400)
    try:
        user = register_user(
            str(body.get("email", "")),
            str(body.get("password", "")),
            str(body.get("display_name", "")),
        )
        token = issue_token(user)
        return _json({"token": token, "user": {"email": user["email"], "display_name": user["display_name"]}})
    except ValueError as e:
        return _json({"error": str(e)}, 400)


@app.route(route="auth/login", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def auth_login(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        body = req.get_json()
    except ValueError:
        return _json({"error": "Invalid JSON"}, 400)
    try:
        user = verify_user(str(body.get("email", "")), str(body.get("password", "")))
        token = issue_token(user)
        return _json({"token": token, "user": {"email": user["email"], "display_name": user["display_name"]}})
    except ValueError as e:
        return _json({"error": str(e)}, 401)


@app.route(route="auth/me", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def auth_me(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        user = _require_user(req)
        return _json({"user": user})
    except PermissionError:
        return _json({"error": "Unauthorized"}, 401)


def _oauth_callback_redirect(token: str) -> func.HttpResponse:
    base = config.FRONTEND_URL.rstrip("/")
    url = f"{base}/index.html#token={urllib.parse.quote(token)}"
    return _redirect(url)


@app.route(route="auth/oauth/google", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def oauth_google_start(req: func.HttpRequest) -> func.HttpResponse:
    try:
        state = issue_oauth_state("google")
        redirect_uri = str(req.url).split("?")[0].replace("/auth/oauth/google", "/auth/oauth/google/callback")
        url = google_authorize_url(redirect_uri, state)
        return _redirect(url)
    except Exception as e:
        return _text(str(e), 500)


@app.route(route="auth/oauth/google/callback", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def oauth_google_callback(req: func.HttpRequest) -> func.HttpResponse:
    try:
        params = req.params or {}
        if params.get("error"):
            return _text(params.get("error_description") or "OAuth error", 400)
        code = params.get("code")
        state = params.get("state")
        if not code or not state:
            return _text("Missing code or state", 400)
        verify_oauth_state(state, "google")
        redirect_uri = str(req.url).split("?")[0]
        info = exchange_google_code(code, redirect_uri)
        user = upsert_oauth_user(info["email"], info["display_name"], "google")
        token = issue_token(user)
        return _oauth_callback_redirect(token)
    except Exception as e:
        logging.exception("Google OAuth failed")
        return _text(str(e), 400)


@app.route(route="auth/oauth/github", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def oauth_github_start(req: func.HttpRequest) -> func.HttpResponse:
    try:
        state = issue_oauth_state("github")
        redirect_uri = str(req.url).split("?")[0].replace("/auth/oauth/github", "/auth/oauth/github/callback")
        url = github_authorize_url(redirect_uri, state)
        return _redirect(url)
    except Exception as e:
        return _text(str(e), 500)


@app.route(route="auth/oauth/github/callback", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def oauth_github_callback(req: func.HttpRequest) -> func.HttpResponse:
    try:
        params = req.params or {}
        if params.get("error"):
            return _text(params.get("error_description") or "OAuth error", 400)
        code = params.get("code")
        state = params.get("state")
        if not code or not state:
            return _text("Missing code or state", 400)
        verify_oauth_state(state, "github")
        redirect_uri = str(req.url).split("?")[0]
        info = exchange_github_code(code, redirect_uri)
        user = upsert_oauth_user(info["email"], info["display_name"], "github")
        token = issue_token(user)
        return _oauth_callback_redirect(token)
    except Exception as e:
        logging.exception("GitHub OAuth failed")
        return _text(str(e), 400)


@app.route(route="analyze", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def analyze(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        _require_user(req)
    except PermissionError:
        return _json({"error": "Unauthorized"}, 401)
    cached = read_insights_cache()
    if not cached:
        df = load_diet_dataframe()
        if df is None:
            return _json({"error": "No cached insights yet. Upload All_Diets.csv or call rebuild-cache."}, 404)
        cached = build_insights_payload(df, source_etag="on-demand")
        try:
            write_insights_cache(cached)
        except Exception:
            logging.exception("Could not persist insights cache")
    diet = (req.params.get("diet") or "all").lower()
    labels = cached.get("labels") or []
    protein = cached.get("protein") or []
    carbs = cached.get("carbs") or []
    fat = cached.get("fat") or []
    if diet != "all":
        idx = [i for i, lab in enumerate(labels) if str(lab).lower() == diet]
        if idx:
            i = idx[0]
            labels = [labels[i]]
            protein = [protein[i]]
            carbs = [carbs[i]]
            fat = [fat[i]]
        else:
            labels, protein, carbs, fat = [], [], [], []
    scatter_all = cached.get("scatter") or []
    scatter = [p for p in scatter_all if diet == "all" or str(p.get("diet", "")).lower() == diet]
    return _json(
        {
            "labels": labels,
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "scatter": scatter[:400],
            "pie": cached.get("pie"),
            "heatmap": cached.get("heatmap"),
            "source_etag": cached.get("source_etag"),
            "generated_at": cached.get("generated_at"),
        }
    )


@app.route(route="insights", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def insights(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        _require_user(req)
    except PermissionError:
        return _json({"error": "Unauthorized"}, 401)
    cached = read_insights_cache()
    if not cached:
        df = load_diet_dataframe()
        if df is None:
            return _json({"error": "No cached insights yet."}, 404)
        cached = build_insights_payload(df, source_etag="on-demand")
        try:
            write_insights_cache(cached)
        except Exception:
            logging.exception("Could not persist insights cache")
    return _json(cached)


@app.route(route="recipes", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def recipes(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        _require_user(req)
    except PermissionError:
        return _json({"error": "Unauthorized"}, 401)
    df = load_diet_dataframe()
    if df is None:
        return _json({"error": "Dataset unavailable"}, 404)
    diet = (req.params.get("diet") or "all").lower()
    q = (req.params.get("q") or "").strip().lower()
    try:
        page = max(1, int(req.params.get("page") or "1"))
        page_size = min(50, max(1, int(req.params.get("page_size") or "10")))
    except ValueError:
        return _json({"error": "Invalid pagination"}, 400)
    filtered = df
    if diet != "all" and "Diet_type" in filtered.columns:
        filtered = filtered[filtered["Diet_type"].str.lower() == diet]
    if q:
        name_col = "Recipe_name" if "Recipe_name" in filtered.columns else None
        if name_col:
            filtered = filtered[filtered[name_col].str.lower().str.contains(q, na=False)]
    total = int(len(filtered))
    start = (page - 1) * page_size
    chunk = filtered.iloc[start : start + page_size]
    cols = [c for c in ["Diet_type", "Recipe_name", "Cuisine_type", "Protein(g)", "Carbs(g)", "Fat(g)"] if c in chunk.columns]
    rows = chunk[cols].to_dict(orient="records") if cols else []
    return _json(
        {
            "items": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }
    )


@app.route(route="clusters", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def clusters(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())
    try:
        _require_user(req)
    except PermissionError:
        return _json({"error": "Unauthorized"}, 401)
    cached = read_insights_cache()
    if not cached:
        df = load_diet_dataframe()
        if df is None:
            return _json({"error": "No data"}, 404)
        cached = build_insights_payload(df, source_etag="on-demand")
    return _json({"clusters": cached.get("clusters") or []})
