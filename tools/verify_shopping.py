"""Run T02 API assertions against a disposable loopback server, using synthetic accounts only.

Creates two local test accounts; never logs credentials and never touches existing accounts.
Usage: python tools/verify_shopping.py --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import http.cookiejar
import ipaddress
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any
from uuid import uuid4


def loopback_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local or parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise argparse.ArgumentTypeError("Only a plain HTTP loopback origin is allowed.")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unexpected redirect from loopback API")


class Account:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect(),
        )
        self.device = str(uuid4())
        self.csrf = ""
        self.revision = 0
        response, _ = self.request("POST", "/api/auth/register", {
            "email": f"shopping-{uuid4().hex}@example.com",
            "password": secrets.token_urlsafe(24), "display_name": "Synthetic shopping check",
        }, expected=201)
        self.csrf = response["csrf_token"]
        self.revision = self.state()["revision"]

    def request(self, method: str, path: str, body: Any = None, *, expected: int = 200,
                headers: dict[str, str] | None = None) -> tuple[Any, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read())
            assert response.status == expected, (path, response.status, expected, payload)
            return payload, response.headers

    def state(self) -> dict[str, Any]:
        return self.request("GET", "/api/state")[0]

    def mutate(self, method: str, path: str, body: Any = None, *, expected: int = 200,
               key: str | None = None, revision: int | None = None) -> tuple[Any, Any]:
        payload, headers = self.request(method, path, body, expected=expected, headers={
            "X-CSRF-Token": self.csrf, "X-Device-ID": self.device,
            "Idempotency-Key": key or str(uuid4()),
            "X-Expected-State-Revision": str(self.revision if revision is None else revision),
        })
        if "X-State-Revision" in headers:
            self.revision = int(headers["X-State-Revision"])
        return payload, headers


def verify(base: str) -> None:
    account, other = Account(base), Account(base)
    initial = account.state()
    ingredient = next(row for row in initial["ingredients"] if row["measurement_family"] == "mass")
    recipe = next(row for row in initial["recipes"] if row["status"] == "published")
    account.mutate("PUT", f"/api/basket/recipes/{recipe['id']}", {"servings": recipe["base_servings"]})
    basket = account.state()["basket"]
    list_id, creation_key = str(uuid4()), str(uuid4())
    before_revision = account.revision
    created, _ = account.mutate("POST", "/api/grocery-lists", {"id": list_id}, expected=201, key=creation_key)
    assert created["status"] == "active" and created["recipes"] == [] and created["items"] == []
    replay, headers = account.mutate("POST", "/api/grocery-lists", {"id": list_id}, expected=201,
                                     key=creation_key, revision=before_revision)
    assert replay == created and headers["X-Idempotent-Replay"] == "true"
    account.mutate("POST", "/api/grocery-lists", {"id": str(uuid4())}, expected=409)
    assert account.state()["basket"] == basket
    other.request("GET", f"/api/grocery-lists/{list_id}", expected=404)
    other.mutate("PATCH", f"/api/grocery-lists/{list_id}", {"title": "Must not change"}, expected=404)
    assert other.state()["grocery_lists"] == []
    error, _ = account.mutate("PATCH", f"/api/grocery-lists/{list_id}", {"title": "Stale"},
                              revision=before_revision, expected=409)
    assert error["code"] == "STATE_REVISION_CONFLICT"
    item_id = str(uuid4())
    body = {"id": item_id, "ingredient_id": ingredient["id"], "label": ingredient["name"],
            "quantity": "1.234", "unit": "kg", "prevent_duplicate_ingredient": True}
    add_key, add_revision = str(uuid4()), account.revision
    added, _ = account.mutate("POST", f"/api/grocery-lists/{list_id}/items", body, expected=201, key=add_key)
    assert Decimal(added["items"][0]["quantity"]) == Decimal("1234.000")
    replay, _ = account.mutate("POST", f"/api/grocery-lists/{list_id}/items", body, expected=201,
                               key=add_key, revision=add_revision)
    assert len(replay["items"]) == 1
    error, _ = account.mutate("POST", f"/api/grocery-lists/{list_id}/items", {**body, "id": str(uuid4())}, expected=409)
    assert error["code"] == "GROCERY_INGREDIENT_ALREADY_LISTED"
    account.mutate("POST", f"/api/grocery-lists/{list_id}/items", {
        "id": str(uuid4()), "ingredient_id": ingredient["id"], "label": ingredient["name"],
        "quantity": "1", "unit": "ml",
    }, expected=422)
    account.mutate("POST", f"/api/grocery-lists/{list_id}/items", {
        "id": str(uuid4()), "label": "Synthetic unknown amount",
    }, expected=201)
    account.mutate("POST", f"/api/grocery-lists/{list_id}/items", {
        "id": str(uuid4()), "label": "Invalid quantity", "quantity": "-1", "unit": "pack",
    }, expected=422)
    account.mutate("POST", f"/api/grocery-lists/{list_id}/items", {
        "id": str(uuid4()), "label": "Incomplete quantity", "quantity": "1",
    }, expected=422)
    account.mutate("PUT", f"/api/grocery-lists/{list_id}/items/{item_id}", {
        "ingredient_id": ingredient["id"], "label": ingredient["name"],
        "quantity": "1234.000", "unit": "g", "checked": True,
    })
    completed, _ = account.mutate("POST", f"/api/grocery-lists/{list_id}/complete", {
        "event_id": str(uuid4()), "pantry_item_ids": [item_id],
    })
    assert completed["status"] == "completed"
    final = account.state()
    pantry = next(row for row in final["pantry_stocks"] if row["ingredient_id"] == ingredient["id"])
    assert Decimal(pantry["quantity"]) == Decimal("1234.000") and final["basket"] == basket
    generated, _ = account.mutate("POST", "/api/grocery-lists/from-basket", {"id": str(uuid4())}, expected=201)
    assert len(generated["recipes"]) == 1 and not account.state()["basket"]["items"]
    print("PASS: manual creation; one active list; replay; account isolation; stale revision; exact metric amount;")
    print("PASS: restock duplicate guard; unknown quantity; invalid quantities/units; completion pantry update;")
    print("PASS: Basket preserved by standalone shopping and existing recipe generation unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", type=loopback_url, default="http://127.0.0.1:8000")
    verify(parser.parse_args().base_url)
