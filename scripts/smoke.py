"""Run the synthetic application path through the public frontend proxy."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("SMART_CREDIT_URL", "http://127.0.0.1:8080").rstrip("/")


def call(method, path, body=None):
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    if method == "POST" and payload is None:
        payload = b""
    request = Request(
        BASE_URL + path,
        data=payload,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {details}") from error


def expect_status(application, expected):
    actual = application.get("status")
    if actual != expected:
        raise AssertionError(f"Expected status {expected!r}, got {actual!r}: {application}")
    print(f"{expected}: {application['id']}")


def main():
    code, application = call(
        "POST",
        "/api/v1/applications",
        {
            "profile_id": "shop",
            "goal_id": "stock",
            "goal_description": "",
            "product_id": "turnover",
            "amount": 1_500_000,
            "term_months": 12,
            "contact_name": "Алия Соколова",
            "contact_role": "Владелец бизнеса",
        },
    )
    if code != 201:
        raise AssertionError(f"Expected HTTP 201, got {code}")
    expect_status(application, "submitted")
    path = f"/api/v1/applications/{application['id']}"

    _, application = call("POST", path + "/evaluate")
    expect_status(application, "approved")

    _, application = call("POST", path + "/accept", {"accepted": True})
    expect_status(application, "signed")

    _, application = call("POST", path + "/disburse")
    expect_status(application, "funded")
    if not application.get("receipt"):
        raise AssertionError("Funded application has no receipt")

    _, restored = call("GET", path)
    expect_status(restored, "funded")
    if not restored.get("receipt"):
        raise AssertionError("Restored application has no receipt")
    print("Полный учебный путь и сохранение результата проверены.")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, RuntimeError, URLError, ValueError, KeyError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        sys.exit(1)
