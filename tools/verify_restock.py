"""T05 checks; disposable accounts on explicit loopback API only."""
from __future__ import annotations

import argparse
from decimal import Decimal
from uuid import uuid4
from verify_shopping import Account, loopback_url


def verify(base: str) -> None:
    account, other = Account(base), Account(base)
    initial = account.state()
    mass = next(row for row in initial['ingredients'] if row['measurement_family'] == 'mass')
    ingredient_id = mass['id']
    path = f'/api/pantry-stocks/{ingredient_id}'
    body = {'measurement_family': 'mass', 'low_stock_threshold': '500.000', 'refill_quantity': '1234.567'}
    account.mutate('PUT', path + '/preferences', body, expected=409)
    account.mutate('PUT', path + '/tracking', {'tracked': True})
    def stock() -> dict:
        return next(row for row in account.state()['pantry_stocks'] if row['ingredient_id'] == ingredient_id)
    assert stock()['low_stock_threshold'] is None and stock()['refill_quantity'] is None
    key, revision = str(uuid4()), account.revision
    saved, _ = account.mutate('PUT', path + '/preferences', body, key=key)
    assert saved['low_stock_threshold'] == '500.000' and saved['refill_quantity'] == '1234.567'
    replay, headers = account.mutate('PUT', path + '/preferences', body, key=key, revision=revision)
    assert replay == saved and headers['X-Idempotent-Replay'] == 'true'
    assert account.revision == revision + 1
    before = account.state()
    for bad in [
        {**body, 'low_stock_threshold': '-1'}, {**body, 'refill_quantity': '-0.001'},
        {**body, 'low_stock_threshold': '0.0001'}, {**body, 'refill_quantity': '1000000000000'},
        {**body, 'measurement_family': 'volume'}, {**body, 'low_stock_threshold': 'NaN'},
    ]:
        account.mutate('PUT', path + '/preferences', bad, expected=422)
        assert account.state() == before
    stale, _ = account.mutate('PUT', path + '/preferences', body, revision=revision, expected=409)
    assert stale['code'] == 'STATE_REVISION_CONFLICT' and account.state() == before
    other.mutate('PUT', path + '/preferences', body, expected=409)
    assert other.state()['pantry_stocks'] == []
    private = str(uuid4())
    account.mutate('POST', '/api/ingredients', {'id': private, 'name': 'Synthetic restock private',
        'category_id': initial['categories'][0]['id'], 'measurement_family': 'mass', 'track_in_pantry': True}, expected=201)
    other.mutate('PUT', f'/api/pantry-stocks/{private}/preferences', body, expected=404)
    private_path = f'/api/pantry-stocks/{private}'
    account.mutate('PUT', private_path + '/preferences', body)
    account.mutate('DELETE', f'/api/ingredients/{private}')
    account.mutate('PUT', private_path + '/preferences', body, expected=409)
    archived = next(row for row in account.state()['pantry_stocks'] if row['ingredient_id'] == private)
    assert archived['refill_quantity'] == '1234.567'
    account.mutate('POST', f'/api/ingredients/{private}/restore')
    restored = next(row for row in account.state()['pantry_stocks'] if row['ingredient_id'] == private)
    assert restored['refill_quantity'] == '1234.567'

    for quantity, flagged in [('501', False), ('500', True), ('499.999', True), ('0', True), ('-1', True)]:
        account.mutate('POST', path + '/operations', {'event_id': str(uuid4()), 'operation': 'set',
            'amount': quantity, 'reason': 'Synthetic threshold boundary'}, expected=201)
        assert (Decimal(stock()['quantity']) <= Decimal(stock()['low_stock_threshold'])) is flagged
        assert stock()['refill_quantity'] == '1234.567'
    account.mutate('POST', '/api/pantry-stocks/bulk', {'event_id': str(uuid4()), 'rows': [
        {'ingredient_id': ingredient_id, 'operation': 'add', 'amount': '0.001', 'unit': 'g', 'pack_count': 1},
    ]}, expected=201)
    assert stock()['refill_quantity'] == '1234.567'
    list_id, item_id = str(uuid4()), str(uuid4())
    account.mutate('POST', '/api/grocery-lists', {'id': list_id}, expected=201)
    account.mutate('POST', f'/api/grocery-lists/{list_id}/items', {
        'id': item_id, 'ingredient_id': ingredient_id, 'label': mass['name'], 'quantity': '1234.567', 'unit': 'g',
    }, expected=201)
    account.mutate('PUT', f'/api/grocery-lists/{list_id}/items/{item_id}', {
        'ingredient_id': ingredient_id, 'label': mass['name'], 'quantity': '1234.567', 'unit': 'g', 'checked': True,
    })
    account.mutate('POST', f'/api/grocery-lists/{list_id}/complete', {
        'event_id': str(uuid4()), 'pantry_item_ids': [item_id],
    })
    assert stock()['low_stock_threshold'] == '500.000' and stock()['refill_quantity'] == '1234.567'
    recipe = next(row for row in initial['recipes'] if row['status'] == 'published')
    account.mutate('POST', f"/api/recipes/{recipe['id']}/cook", {'event_id': str(uuid4()), 'servings': recipe['base_servings']})
    assert stock()['low_stock_threshold'] == '500.000' and stock()['refill_quantity'] == '1234.567'
    account.mutate('POST', path + '/operations', {'event_id': str(uuid4()), 'operation': 'set', 'amount': '0'}, expected=201)
    account.mutate('PUT', path + '/preferences', {**body, 'low_stock_threshold': '0', 'refill_quantity': '0'})
    account.mutate('PUT', path + '/tracking', {'tracked': False})
    account.mutate('PUT', path + '/tracking', {'tracked': True})
    assert stock()['low_stock_threshold'] is None and stock()['refill_quantity'] is None
    local = {'categories': [{'id': 'legacy-category', 'name': initial['categories'][0]['name'], 'scope': 'global'}],
        'ingredients': [{'id': 'legacy-mass', 'name': mass['name'], 'categoryId': 'legacy-category',
                         'family': 'mass', 'scope': 'global'}],
        'trackedIngredientIds': ['legacy-mass'], 'balances': {'legacy-mass': '500000'},
        'pantryPreferences': {'legacy-mass': {'lowStockThreshold': '500000', 'refillQuantity': '1234567'}}}
    def import_state(state: dict) -> dict:
        import_id = str(uuid4())
        return account.mutate('POST', '/api/imports/local-state', {'import_id': import_id, 'state': state}, key=import_id)[0]
    imported = import_state(local)
    assert imported['id_map']['legacy-mass'] == ingredient_id and not imported['conflicts']
    assert stock()['low_stock_threshold'] == '500.000' and stock()['refill_quantity'] == '1234.567'
    local['pantryPreferences']['legacy-mass']['refillQuantity'] = '2000'
    assert any(item['kind'] == 'pantryPreferences' for item in import_state(local)['conflicts'])
    local['pantryPreferences']['legacy-mass']['refillQuantity'] = '-1'
    assert any(item['kind'] == 'pantryPreferences' for item in import_state(local)['conflicts'])
    assert stock()['refill_quantity'] == '1234.567'
    del local['pantryPreferences']
    assert not import_state(local)['conflicts']
    print('PASS restock: defaults, exact thresholds/refill, boundary, validation, replay/revision, ownership, '
          'stock/cook retention, untrack reset, legacy ID import and conservative preference conflicts')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, type=loopback_url)
    verify(parser.parse_args().base_url)
