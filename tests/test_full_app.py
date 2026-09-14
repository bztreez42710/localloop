import os
from pathlib import Path

DB = Path('/tmp/localloop-test.db')
try:
    DB.unlink()
except FileNotFoundError:
    pass

os.environ['LOCALLOOP_DB'] = str(DB)
os.environ['LOCALLOOP_SECRET'] = 'test-secret'
os.environ['LOCALLOOP_COOKIE_SECURE'] = '0'
os.environ['LOCALLOOP_OWNER_EMAIL'] = 'owner@test.local'
os.environ['LOCALLOOP_OWNER_PASSWORD'] = 'OwnerPass123!'
os.environ['LOCALLOOP_SAFETY_EMAIL'] = 'safety@test.local'
os.environ['LOCALLOOP_SAFETY_PASSWORD'] = 'SafetyPass123!'
os.environ['LOCALLOOP_DEVELOPER_EMAIL'] = 'developer@test.local'
os.environ['LOCALLOOP_DEVELOPER_PASSWORD'] = 'DeveloperPass123!'

from fastapi.testclient import TestClient
from app.portal import app


def login(client, email, password, portal='any'):
    return client.post('/login', data={'email': email, 'password': password, 'portal': portal}, follow_redirects=False)


def register(client, email, password, name, role, business_name=''):
    return client.post('/register', data={'email': email, 'password': password, 'name': name, 'role': role, 'business_name': business_name}, follow_redirects=False)


def test_every_role_and_core_workflows():
    with TestClient(app) as c:
        assert c.get('/').status_code == 200
        assert c.get('/login').status_code == 200
        assert c.get('/register').status_code == 200

        # Owner/admin login and dashboard
        r = login(c, 'owner@test.local', 'OwnerPass123!', 'admin')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        assert c.get('/shopping/ops').status_code == 200
        c.post('/logout')

        # Safety and developer are read-only
        r = login(c, 'safety@test.local', 'SafetyPass123!', 'safety')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        assert c.get('/shopping/ops').status_code == 200
        assert c.post('/deliveries', data={'pickup':'A','dropoff':'B','item_description':'X','distance_miles':'1','notes':''}).status_code == 403
        c.post('/logout')

        r = login(c, 'developer@test.local', 'DeveloperPass123!', 'developer')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        c.post('/logout')

        # Customer account and one-place delivery
        r = register(c, 'customer@test.local', 'Customer123!', 'Test Customer', 'customer')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        r = c.post('/deliveries', data={'pickup':'100 N Howard St, Spokane, WA','dropoff':'200 N Wall St, Spokane, WA','item_description':'Test package','distance_miles':'2','notes':'test'}, follow_redirects=False)
        assert r.status_code == 303

        # Multi-store shopping order
        assert c.get('/shop').status_code == 200
        r = c.post('/shop/orders', data={
            'dropoff':'300 W Riverside Ave, Spokane, WA',
            'store1':'Store One','address1':'Spokane, WA','items1':'milk\nbread',
            'store2':'Store Two','address2':'Spokane, WA','items2':'soap',
            'store3':'','address3':'','items3':'','notes':'test order'
        }, follow_redirects=False)
        assert r.status_code == 303
        c.post('/logout')

        # Business/store login side
        r = register(c, 'store@test.local', 'StorePass123!', 'Test Store', 'business', 'Test Store')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        assert c.get('/shop').status_code == 200
        c.post('/logout')

        # Driver claims both a delivery and shopping run
        r = register(c, 'driver@test.local', 'DriverPass123!', 'Test Driver', 'driver')
        assert r.status_code == 303
        assert c.get('/dashboard').status_code == 200
        assert c.get('/driver/shop').status_code == 200

        # Claim normal delivery #1
        r = c.post('/deliveries/1/accept', follow_redirects=False)
        assert r.status_code == 303
        assert c.post('/deliveries/1/status', data={'status':'picked_up','proof':''}, follow_redirects=False).status_code == 303
        assert c.post('/deliveries/1/status', data={'status':'delivered','proof':'handed to customer'}, follow_redirects=False).status_code == 303

        # Claim shopping order #1 and complete lifecycle
        assert c.post('/shop/orders/1/accept', follow_redirects=False).status_code == 303
        assert c.post('/shop/orders/1/status', data={'status':'shopping'}, follow_redirects=False).status_code == 303
        assert c.post('/shop/orders/1/status', data={'status':'delivering'}, follow_redirects=False).status_code == 303
        assert c.post('/shop/orders/1/status', data={'status':'delivered'}, follow_redirects=False).status_code == 303

        # Wrong portal should be rejected
        c.post('/logout')
        r = login(c, 'driver@test.local', 'DriverPass123!', 'customer')
        assert r.status_code == 403
