#!/usr/bin/env python3
"""Keep Render and Supabase warm by periodically pinging both services."""

import os
import sys
from urllib import error, request


TIMEOUT_SECONDS = int(os.environ.get("KEEPALIVE_TIMEOUT", "20"))


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"[ERROR] Missing required environment variable: {name}")
        sys.exit(2)
    return value


def ping(url: str, headers: dict | None = None) -> tuple[int | None, str]:
    req = request.Request(
        url,
        headers={
            "User-Agent": "mindcare-keepalive/1.0",
            **(headers or {}),
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(180).decode("utf-8", errors="ignore").strip()
            return resp.status, body
    except error.HTTPError as exc:
        try:
            body = exc.read(180).decode("utf-8", errors="ignore").strip()
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:
        return None, str(exc)


def is_awake(status: int | None) -> bool:
    # 2xx/3xx/4xx means the service responded. 5xx/None means unavailable.
    return status is not None and status < 500


def main() -> int:
    render_base = required_env("KEEPALIVE_RENDER_URL").rstrip("/")
    supabase_base = required_env("KEEPALIVE_SUPABASE_URL").rstrip("/")
    supabase_key = os.environ.get("KEEPALIVE_SUPABASE_KEY", "").strip()

    render_url = f"{render_base}/healthz"
    supabase_url = f"{supabase_base}/rest/v1/"

    render_status, render_info = ping(render_url)

    supabase_headers = {}
    if supabase_key:
        supabase_headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }
    supabase_status, supabase_info = ping(supabase_url, headers=supabase_headers)

    print(f"[Render]   {render_url} -> {render_status} {render_info[:120]}")
    print(f"[Supabase] {supabase_url} -> {supabase_status} {supabase_info[:120]}")

    render_ok = is_awake(render_status)
    supabase_ok = is_awake(supabase_status)

    if render_ok and supabase_ok:
        print("[OK] Keep-alive ping succeeded for both services.")
        return 0

    print("[ERROR] Keep-alive failed for one or more services.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
