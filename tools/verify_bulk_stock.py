"""Atomic T03 API scenarios. Disposable synthetic accounts on explicit loopback only.
Usage: python tools/verify_bulk_stock.py --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from uuid import uuid4

from verify_shopping import Account, loopback_url


def verify(base: str) -> None:
    account, other = Account(base), Account(base)
    initial = account.state()
    mass = next(row for row in initial["ingredients"] if row["measurement_family"] == "mass")
    volume = next(row for row in initial["ingredients"] if row["measurement_family"] == "volume")
    count = next(row for row in initial["ingredients"] if row["measurement_family"] == "count")
    rows = [
        {"ingredient_id": mass["id"], "operation": "set", "amount": "0.5", "unit": "kg", "pack_count": 2},
        {"ingredient_id": volume["id"], "operation": "add", "amount": "0.125", "unit": "L", "pack_count": 3},
        {"ingredient_id": count["id"], "operation": "set", "amount": "0.5", "unit": "item", "pack_count": 3},
    ]
    body = {"event_id": str(uuid4()), "rows": rows}
    key, revision = str(uuid4()), account.revision
    result, _ = account.mutate("POST", "/api/pantry-stocks/bulk", body, expected=201, key=key)
    assert len(result["changes"]) == 3 and result["type"] == "manual"
    changes = {row["ingredient_id"]: row for row in result["changes"]}
    assert changes[mass["id"]]["after"] == "1000.000"
    assert changes[volume["id"]]["after"] == "375.000"
    assert changes[count["id"]]["after"] == "1.500"
    assert account.revision == revision + 1
    replay, headers = account.mutate("POST", "/api/pantry-stocks/bulk", body, expected=201,
                                     key=key, revision=revision)
    assert replay == result and headers["X-Idempotent-Replay"] == "true"
    assert len(account.state()["activity"]) == 1
    before = account.state()

    def rejected(bad_rows: list[dict[str, object]], expected: int = 422) -> None:
        account.mutate("POST", "/api/pantry-stocks/bulk", {"event_id": str(uuid4()), "rows": bad_rows},
                       expected=expected)
        after = account.state()
        assert after == before, "Rejected batch changed account state"

    # Every invalid row follows a valid Add, so this detects a partial commit.
    first = {**rows[0], "operation": "add", "amount": "2"}
    for bad in [
        {**rows[1], "unit": "g"},
        {**rows[1], "amount": "0.0000001"},
        {**rows[1], "amount": "0.000001", "unit": "ml"},
        {**rows[1], "amount": "0"},
        {**rows[1], "amount": "1e3"},
        {**rows[1], "amount": "999999999999", "unit": "L"},
        {**rows[1], "pack_count": 0},
        {**rows[1], "pack_count": 1.5},
        {**rows[1], "pack_count": True},
        {**rows[1], "ingredient_id": str(uuid4())},
    ]:
        rejected([first, bad], 404 if bad["ingredient_id"] not in {mass["id"], volume["id"], count["id"]} else 422)
    rejected([first, first])
    rejected([])
    rejected([first] * 101)
    # Account-specific ingredient cannot be adjusted by the other account.
    custom_id = str(uuid4())
    account.mutate("POST", "/api/ingredients", {
        "id": custom_id, "name": "Synthetic private bulk ingredient", "category_id": initial["categories"][0]["id"],
        "measurement_family": "mass", "track_in_pantry": False,
    }, expected=201)
    other_before = other.state()
    other.mutate("POST", "/api/pantry-stocks/bulk", {"event_id": str(uuid4()), "rows": [
        first, {**rows[1], "ingredient_id": custom_id},
    ]}, expected=404)
    assert other.state() == other_before
    other.request("GET", f"/api/activity/{body['event_id']}", expected=404)
    account.mutate("POST", "/api/pantry-stocks/bulk", {"event_id": str(uuid4()), "rows": rows},
                   revision=revision, expected=409)
    assert len(account.state()["activity"]) == 1
    # Require a revision even if a caller bypasses the PWA.
    account.request("POST", "/api/pantry-stocks/bulk", {"event_id": str(uuid4()), "rows": rows},
                    expected=422, headers={"X-CSRF-Token": account.csrf, "X-Device-ID": account.device,
                                           "Idempotency-Key": str(uuid4())})
    # Precision checks apply to the final product, including a sub-minor pack size.
    exact = {**rows[0], "amount": "0.0005", "unit": "g", "pack_count": 2}
    precise, _ = account.mutate("POST", "/api/pantry-stocks/bulk",
                                {"event_id": str(uuid4()), "rows": [exact]}, expected=201)
    assert precise["changes"][0]["after"] == "0.001"
    # Overflow of resulting balance or history delta rolls back the full batch.
    limit = {**rows[0], "amount": "999999999999.999", "unit": "g", "pack_count": 1}
    account.mutate("POST", "/api/pantry-stocks/bulk", {"event_id": str(uuid4()), "rows": [limit]}, expected=201)
    before = account.state()
    rejected([{**rows[1], "amount": "1"},
              {**rows[0], "operation": "add", "amount": "0.001", "unit": "g", "pack_count": 1}])
    final_stock = next(row for row in account.state()["pantry_stocks"] if row["ingredient_id"] == mass["id"])
    assert Decimal(final_stock["quantity"]) == Decimal("999999999999.999")
    # Manual batch history keeps existing reversal policy; cooking still reverses.
    account.mutate("POST", f"/api/activity/{body['event_id']}/reverse", {"event_id": str(uuid4())}, expected=409)
    recipe = next(row for row in initial["recipes"] if row["status"] == "published")
    before_cooking = {row["ingredient_id"]: Decimal(row["quantity"]) for row in account.state()["pantry_stocks"]}
    cooked, _ = account.mutate("POST", f"/api/recipes/{recipe['id']}/cook",
                               {"event_id": str(uuid4()), "servings": recipe["base_servings"]})
    account.mutate("POST", f"/api/activity/{cooked['id']}/reverse", {"event_id": str(uuid4())})
    restored = {row["ingredient_id"]: Decimal(row["quantity"]) for row in account.state()["pantry_stocks"]}
    for change in cooked["changes"]:
        assert restored[change["ingredient_id"]] == before_cooking.get(change["ingredient_id"], Decimal(0))
    print("PASS bulk: 3-row exact Add/Set, packs, one event/revision, replay, rollback, "
          "duplicates, precision, range, ownership, stale/missing revision, existing reversal policy")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=loopback_url)
    verify(parser.parse_args().base_url)
