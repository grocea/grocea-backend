"""T04 real loopback PostgreSQL/API/SMTP integration. Generated accounts only.
Run with DATABASE_URL pointing to grocea_next_phase_20261016 and PYTHONPATH=src.
--serve keeps the fixture API, SMTP and private loopback browser-fixture control alive.
No credentials, bearer tokens, addresses or mail bodies are printed.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import delete, select, text, update
from grocea.account import recovery_settings, send_mail
from grocea.auth import password_hash
from grocea.config import get_settings
from grocea.db import SessionFactory
from grocea.models import *  # local fixture construction only

BASE = 'http://127.0.0.1:8000'
MAIL: list = []
condition = threading.Condition()

class SMTP(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 loopback fixture\r\n')
        while True:
            line = self.rfile.readline()
            if not line: return
            cmd = line.upper()
            if cmd.startswith((b'EHLO', b'HELO')): self.wfile.write(b'250 fixture\r\n')
            elif cmd.startswith(b'DATA'):
                self.wfile.write(b'354 data\r\n'); parts = []
                while True:
                    part = self.rfile.readline()
                    if not part or part == b'.\r\n': break
                    parts.append(part)
                with condition:
                    MAIL.append(BytesParser(policy=policy.default).parsebytes(b''.join(parts)))
                    condition.notify_all()
                self.wfile.write(b'250 accepted\r\n')
            elif cmd.startswith(b'QUIT'): self.wfile.write(b'221 bye\r\n'); return
            else: self.wfile.write(b'250 ok\r\n')

class Client:
    def __init__(self, register=True):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.email = f'account-{uuid4().hex}@example.com'; self.password = secrets.token_urlsafe(24); self.csrf = ''
        self.uid = None
        if register:
            _, value = self.req('POST', '/api/auth/register', {'email': self.email, 'password': self.password,
                'display_name': 'Synthetic account check'})
            self.csrf = value['csrf_token']; self.uid = UUID(value['account']['id'])
    def req(self, method, path, body=None, headers=None):
        request = urllib.request.Request(BASE + path, method=method,
            data=None if body is None else json.dumps(body).encode(), headers={
                'Content-Type':'application/json', 'X-CSRF-Token': self.csrf, **(headers or {})})
        try: response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as failure: response = failure
        with response:
            raw=response.read(); return response.status, json.loads(raw) if raw else None
    def login(self, password=None):
        status, value=self.req('POST','/api/auth/login',{'email':self.email,'password':password or self.password})
        if status==200:self.csrf=value['csrf_token']
        return status
    def reset_token(self):
        status,_=self.req('POST','/api/auth/password-reset/request',{'email':self.email}); assert status==202
        with SessionFactory() as session:
            target=session.scalar(select(PasswordResetToken.token_hash).where(PasswordResetToken.user_id==self.uid).order_by(PasswordResetToken.created_at.desc()).limit(1))
        end=time.monotonic()+5
        with condition:
            while time.monotonic()<end:
                for message in reversed(MAIL):
                    if message['To']==self.email and message['Subject']=='Reset your Grocea password':
                        candidate=re.search(r'#token=([A-Za-z0-9_-]+)',message.get_content()).group(1)
                        if hashlib.sha256(candidate.encode()).digest()==target:return candidate
                condition.wait(.1)
        raise AssertionError('Loopback reset mail was not received')
    def remove(self):return self.req('DELETE','/api/account',{'current_password':self.password,'confirmation':'DELETE'})[0]

def check_config():
    settings=get_settings(); saved=settings.public_app_origin
    for bad in ['', 'https:///', 'http://example.com', 'https://[bad', 'https://host:bad', 'https://host/a']:
        settings.public_app_origin=bad
        try: recovery_settings(); raise AssertionError('Invalid origin accepted')
        except Exception as error: assert getattr(error,'code',None)=='RECOVERY_UNAVAILABLE'
    settings.public_app_origin=saved
    saved_host=settings.smtp_host;settings.smtp_host=''
    assert not send_mail('synthetic@example.com','fixture','fixture')
    settings.smtp_host=saved_host
    class Stall(socketserver.BaseRequestHandler):
        def handle(self): time.sleep(1)
    stall=socketserver.ThreadingTCPServer(('127.0.0.1',8031),Stall);stall.daemon_threads=True
    threading.Thread(target=stall.serve_forever,daemon=True).start()
    saved_port=settings.smtp_port;saved_timeout=settings.smtp_timeout_seconds
    settings.smtp_port=8031;settings.smtp_timeout_seconds=.15
    started=time.monotonic();assert not send_mail('synthetic@example.com','fixture','fixture');assert time.monotonic()-started<1
    settings.smtp_port=saved_port;settings.smtp_timeout_seconds=saved_timeout
    stall.shutdown();stall.server_close()
    print('PASS malformed/missing configuration, real stalled SMTP timeout and redacted transport failure')

def graph(client):
    with SessionFactory() as session:
        uid=client.uid; cat=Category(user_id=uid,name='Synthetic archived',normalized_name=f'synthetic-{uuid4().hex}',archived_at=datetime.now(UTC))
        session.add(cat);session.flush()
        ing=Ingredient(user_id=uid,category_id=cat.id,name='Synthetic ingredient',normalized_name=f'synthetic-{uuid4().hex}',measurement_family='mass',archived_at=datetime.now(UTC))
        session.add(ing);session.flush()
        recipe=Recipe(user_id=uid,status='draft',name='Synthetic draft',description='fixture',base_servings=2,archived_at=datetime.now(UTC))
        session.add(recipe);session.flush()
        session.add_all([RecipeIngredient(recipe_id=recipe.id,ingredient_id=ing.id,position=0,quantity_input='1.234',quantity=Decimal('1.234'),unit='g'),RecipeStep(recipe_id=recipe.id,position=0,body='Synthetic step'),PantryStock(user_id=uid,ingredient_id=ing.id,quantity=Decimal('-1.234'),low_stock_threshold=Decimal('0'),refill_quantity=Decimal('1234.567')),BasketItem(user_id=uid,recipe_id=recipe.id,servings=2,position=0)])
        a=ActivityEvent(user_id=uid,event_type='manual',title='Synthetic first',detail='',recipe_id=recipe.id)
        b=ActivityEvent(user_id=uid,event_type='manual',title='Synthetic second',detail='')
        session.add_all([a,b]);session.flush();a.reversal_of=b.id;b.reversal_of=a.id
        session.add(StockChange(event_id=a.id,ingredient_id=ing.id,before=Decimal('0'),delta=Decimal('-1.234'),after=Decimal('-1.234')))
        for status in ['active','completed']:
            grocery=GroceryList(user_id=uid,title='Synthetic list',status=status,completed_at=datetime.now(UTC) if status=='completed' else None)
            session.add(grocery);session.flush()
            session.add(GroceryListRecipe(grocery_list_id=grocery.id,recipe_id=recipe.id,recipe_snapshot_id=recipe.id,recipe_name=recipe.name,position=0,servings=2,base_servings=2))
            item=GroceryListItem(grocery_list_id=grocery.id,ingredient_id=ing.id,original_ingredient_id=ing.id,label='Synthetic item',category_name=cat.name,measurement_family='mass',quantity=Decimal('2.345'),unit='g',checked=True,origin='generated',edited=True,original_required=Decimal('3.456'),original_pantry=Decimal('1.111'),original_quantity=Decimal('2.345'))
            session.add(item);session.flush()
            session.add(GroceryListItemSource(grocery_list_item_id=item.id,recipe_snapshot_id=recipe.id,recipe_name=recipe.name,servings=2,quantity=Decimal('3.456'),unit='g'))
        session.add(ProcessedMutation(user_id=uid,device_id=uuid4(),mutation_id=uuid4(),mutation_type='fixture',response_body={'fixture':'private replay'},revision=0))
        session.commit();return ing.id,recipe.id

def verify():
    check_config();a=Client();b=Client();anonymous=Client(False)
    started=time.monotonic()
    known=a.req('POST','/api/auth/password-reset/request',{'email':a.email})
    assert time.monotonic()-started>=.14
    started=time.monotonic()
    unknown=anonymous.req('POST','/api/auth/password-reset/request',{'email':f'unknown-{uuid4().hex}@example.com'})
    assert time.monotonic()-started>=.14
    assert known==unknown
    token=a.reset_token()
    second_session=Client(False);second_session.email=a.email;second_session.password=a.password;assert second_session.login()==200
    with SessionFactory() as session:
        record=session.scalar(select(PasswordResetToken).where(PasswordResetToken.user_id==a.uid));assert record.token_hash==hashlib.sha256(token.encode()).digest()
    new_password=secrets.token_urlsafe(24)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:Client(False).req('POST','/api/auth/password-reset/confirm',{'token':token,'new_password':new_password})[0],range(2)))
    assert sorted(results)==[204,400]
    assert a.req('GET','/api/state')[0]==401 and a.login()==401
    assert second_session.req('GET','/api/state')[0]==401
    with SessionFactory() as session:assert session.scalar(select(PasswordResetToken).where(PasswordResetToken.user_id==a.uid)) is None
    assert a.login(new_password)==200;a.password=new_password
    assert a.req('POST','/api/auth/password-reset/confirm',{'token':token,'new_password':new_password})[0]==400
    assert a.req('POST','/api/auth/password-reset/confirm',{'token':'☃','new_password':new_password})[0]==400
    with SessionFactory() as session:
        expired=secrets.token_urlsafe(32);session.add(PasswordResetToken(user_id=a.uid,token_hash=hashlib.sha256(expired.encode()).digest(),expires_at=datetime.now(UTC)-timedelta(seconds=1)));session.commit()
    assert a.req('POST','/api/auth/password-reset/confirm',{'token':expired,'new_password':new_password})[0]==400
    print('PASS identical request shape, real SMTP link, hashed storage, concurrent single use, expiry/replay, revoked sessions and normal login')
    # Persisted cooldown cannot issue another message/token.
    before=len(MAIL)
    for _ in range(3):assert a.req('POST','/api/auth/password-reset/request',{'email':a.email})[0]==202
    time.sleep(.15);assert len(MAIL)==before
    # Bounded expiry pruning.
    with SessionFactory() as session:
        session.add(RecoveryThrottle(action='fixture',subject_hash=b'x'*32,window_start=datetime.now(UTC)-timedelta(days=3),count=1,expires_at=datetime.now(UTC)-timedelta(days=2)));session.commit()
    a.req('POST','/api/auth/password-reset/request',{'email':a.email})
    with SessionFactory() as session:assert session.scalar(select(RecoveryThrottle).where(RecoveryThrottle.action=='fixture')) is None
    print('PASS shared-store cooldown and indexed expiry pruning')
    ing,recipe=graph(a);before_b=b.req('GET','/api/account/export')[1]
    status,data=a.req('GET','/api/account/export');assert status==200 and data['version']==1 and not data['restore_supported']
    pantry=data['data']['pantry_stocks'][0];assert pantry['quantity']=='-1.234' and pantry['low_stock_threshold']=='0.000' and pantry['refill_quantity']=='1234.567'
    assert len(data['data']['grocery_lists'])==2 and len(data['data']['grocery_list_item_sources'])==2
    assert any(row['id']==str(recipe) and row['archived_at'] for row in data['data']['recipes'])
    assert next(row for row in data['data']['recipe_ingredients'] if row['recipe_id']==str(recipe))['quantity_input']=='1.234'
    assert not any(secret in json.dumps(data) for secret in ['password_hash','csrf_token','token_hash','private replay'])
    assert str(b.uid) not in json.dumps(data)
    assert a.req('DELETE','/api/account',{'current_password':'wrong','confirmation':'DELETE'})[0]==401
    assert a.req('GET','/api/auth/session')[0]==200
    assert a.req('DELETE','/api/account',{'current_password':a.password,'confirmation':'NO'})[0]==422
    # Surviving foreign reference must not be SET NULL by teardown.
    with SessionFactory() as session:
        foreign=GroceryList(user_id=b.uid,title='Synthetic foreign reference',status='active');session.add(foreign);session.flush()
        foreign_id=foreign.id
        session.add(GroceryListItem(grocery_list_id=foreign.id,ingredient_id=ing,label='Synthetic inconsistent reference',origin='manual'));session.commit()
    assert a.remove()==409
    assert a.req('GET','/api/auth/session')[0]==200
    with SessionFactory() as session:session.execute(delete(GroceryList).where(GroceryList.id==foreign_id));session.commit()
    assert a.remove()==204 and a.req('GET','/api/state')[0]==401
    with SessionFactory() as session:
        assert session.get(User,a.uid) is None
        for model in [Category,Ingredient,Recipe,PantryStock,ActivityEvent,AuthSession,PasswordResetToken,ProcessedMutation,BasketItem,GroceryList]:
            assert session.scalar(select(model).where(model.user_id==a.uid)) is None
    after_b=b.req('GET','/api/account/export')[1]
    assert before_b['data']==after_b['data']
    print('PASS archive-inclusive exact export, wrong-password retention, ownership rollback, full deletion including reversal cycle; other account/global preservation')
    # Actual old-auth HTTP request blocked on account lock then sees revocation.
    race=Client()
    with SessionFactory() as session, concurrent.futures.ThreadPoolExecutor(1) as pool:
        user=session.scalar(select(User).where(User.id==race.uid).with_for_update())
        future=pool.submit(race.req,'GET','/api/account/export')
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with SessionFactory() as observer:
                waiting=observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%users%FOR UPDATE%'"))
            if waiting:break
            time.sleep(.02)
        assert waiting
        session.execute(delete(AuthSession).where(AuthSession.user_id==race.uid));session.commit()
        assert future.result()[0]==401
    race.login();assert race.remove()==204
    assert b.remove()==204
    # After-lock validation applies to mutation replay and new writes too.
    for replay in [False, True]:
        writer=Client();key=str(uuid4());device=str(uuid4())
        headers={'Idempotency-Key':key,'X-Device-ID':device}
        payload={'display_name':'Synthetic profile','preferred_servings':2}
        if replay:assert writer.req('PATCH','/api/profile',payload,headers)[0]==200
        with SessionFactory() as session, concurrent.futures.ThreadPoolExecutor(1) as pool:
            user=session.scalar(select(User).where(User.id==writer.uid).with_for_update())
            revision=user.state_revision
            future=pool.submit(writer.req,'PATCH','/api/profile',payload,headers)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                with SessionFactory() as observer:
                    waiting=observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%users%FOR UPDATE%'"))
                if waiting:break
                time.sleep(.02)
            assert waiting
            session.execute(delete(AuthSession).where(AuthSession.user_id==writer.uid));session.commit()
            assert future.result()[0]==401
        with SessionFactory() as session:assert session.get(User,writer.uid).state_revision==revision
        writer.login();assert writer.remove()==204
    login_race=Client();replacement=secrets.token_urlsafe(24)
    with SessionFactory() as session, concurrent.futures.ThreadPoolExecutor(1) as pool:
        user=session.scalar(select(User).where(User.id==login_race.uid).with_for_update())
        future=pool.submit(login_race.login)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with SessionFactory() as observer:
                waiting=observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%users%FOR UPDATE%'"))
            if waiting:break
            time.sleep(.02)
        assert waiting;user.password_hash=password_hash.hash(replacement);session.execute(delete(AuthSession).where(AuthSession.user_id==login_race.uid));session.commit()
        assert future.result()==401
    login_race.password=replacement;assert login_race.login()==200;assert login_race.remove()==204
    req=urllib.request.Request(BASE+'/api/account/export',method='OPTIONS',headers={'Origin':'http://127.0.0.1:5184','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'x-account-id'})
    with urllib.request.urlopen(req) as response:assert response.status==200 and 'X-Account-ID' in response.headers['Access-Control-Allow-Headers']
    print('PASS login refresh after password replacement and X-Account-ID CORS preflight')
    print('PASS revoked-session new writes and cached replay rejected before revision or replay access')
    print('PASS revoked session revalidated after real PostgreSQL lock wait')

FIXTURE=None
class Control(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!='/fixture':self.send_error(404);return
        data={'email':FIXTURE.email,'password':FIXTURE.password,'userId':str(FIXTURE.uid)}
        if 'reset' in self.headers.get('X-Fixture-Action',''):
            with SessionFactory() as session:
                session.execute(delete(RecoveryThrottle).where(RecoveryThrottle.action.in_(['request-email','request-cooldown']),RecoveryThrottle.subject_hash==hmac.digest(get_settings().recovery_throttle_secret.get_secret_value().encode(),FIXTURE.email.encode(),'sha256')));session.commit()
            data['token']=FIXTURE.reset_token()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    assert os.environ.get('DATABASE_URL')=='postgresql+psycopg://127.0.0.1:5432/grocea_next_phase_20261016'
    os.environ.update(APP_ENV='local',PUBLIC_APP_ORIGIN='http://127.0.0.1:5184',SMTP_HOST='127.0.0.1',SMTP_PORT='8025',SMTP_SECURITY='none',SMTP_FROM='fixture@example.com',RECOVERY_THROTTLE_SECRET='local-fixture-only',CORS_ORIGINS='http://127.0.0.1:5184,http://localhost:5173',SMTP_TIMEOUT_SECONDS='0.5')
    get_settings.cache_clear()
    # Reset only this fixture secret's loopback IP buckets between repeated runs.
    with SessionFactory() as session:
        peer_hash=hmac.digest(b'local-fixture-only',b'127.0.0.1','sha256')
        session.execute(delete(RecoveryThrottle).where(RecoveryThrottle.subject_hash==peer_hash, RecoveryThrottle.action.in_(['request-ip','confirm-ip'])))
        session.commit()
    smtp=socketserver.ThreadingTCPServer(('127.0.0.1',8025),SMTP);smtp.daemon_threads=True
    threading.Thread(target=smtp.serve_forever,daemon=True).start()
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    api=subprocess.Popen([sys.executable,'-m','uvicorn','grocea.main:app','--host','127.0.0.1','--port','8000','--no-access-log','--log-level','warning'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:urllib.request.urlopen(BASE+'/api/health/ready',timeout=1);break
            except (OSError,urllib.error.URLError):time.sleep(.1)
        verify()
        if args.serve:
            FIXTURE=Client();control=ThreadingHTTPServer(('127.0.0.1',8030),Control)
            print('READY loopback browser fixture; no credentials logged',flush=True);control.serve_forever()
    finally:
        api.terminate();api.wait(timeout=10);smtp.shutdown();smtp.server_close()
