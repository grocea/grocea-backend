"""Universal HTTP recovery failure with a missing transport; loopback fixture only."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

assert os.environ.get('DATABASE_URL') == 'postgresql+psycopg://127.0.0.1:5432/grocea_next_phase_20261016'
from verify_account import Client
fixture = Client()
known = fixture.email
env = {**os.environ, 'APP_ENV': 'local', 'SMTP_HOST': '', 'PUBLIC_APP_ORIGIN': '',
       'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'grocea.main:app', '--host', '127.0.0.1',
                           '--port', '8001', '--no-access-log', '--log-level', 'warning'],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen('http://127.0.0.1:8001/api/health/ready', timeout=1)
            break
        except (OSError, urllib.error.URLError):
            time.sleep(.1)
    results = []
    for email in [known, 'unknown-config-fixture@example.com']:
        request = urllib.request.Request('http://127.0.0.1:8001/api/auth/password-reset/request', method='POST',
            headers={'Content-Type': 'application/json'}, data=json.dumps({'email': email}).encode())
        try:
            urllib.request.urlopen(request)
            raise AssertionError('Unconfigured recovery accepted request')
        except urllib.error.HTTPError as response:
            body = json.load(response)
            results.append((response.status, body['code'], body['message'], response.headers['Cache-Control']))
    assert results[0] == results[1] and results[0][0:2] == (503, 'RECOVERY_UNAVAILABLE')
    print('PASS universal HTTP503/no-store for generated known and unknown emails with missing SMTP configuration')
finally:
    process.terminate()
    process.wait(timeout=10)
    assert fixture.remove() == 204
