import os
from pathlib import Path

DB = Path('/tmp/localloop-test.db')
try: DB.unlink()
except FileNotFoundError: pass
os.environ['LOCALLOOP_DB']=str(DB)
os.environ['LOCALLOOP_SECRET']='test-secret'
os.environ['LOCALLOOP_COOKIE_SECURE']='0'
os.environ['LOCALLOOP_OWNER_EMAIL']='owner@test.local'
os.environ['LOCALLOOP_OWNER_PASSWORD']='OwnerPass123!'
os.environ['LOCALLOOP_SAFETY_EMAIL']='safety@test.local'
os.environ['LOCALLOOP_SAFETY_PASSWORD']='SafetyPass123!'
os.environ['LOCALLOOP_DEVELOPER_EMAIL']='developer@test.local'
os.environ['LOCALLOOP_DEVELOPER_PASSWORD']='DeveloperPass123!'
from fastapi.testclient import TestClient
from app.portal import app

def login(c,e,p,portal='any'): return c.post('/login',data={'email':e,'password':p,'portal':portal},follow_redirects=False)
def register(c,e,p,n,r,b=''):
    return c.post('/register',data={'email':e,'password':p,'name':n,'role':r,'business_name':b,'age_18':'yes','accept_terms':'yes','accept_privacy':'yes','accept_driver':'yes'},follow_redirects=False)
def accept_legal(c): return c.post('/legal/acceptance',data={'accept_terms':'yes','accept_privacy':'yes','accept_driver':'yes'},follow_redirects=False)

def test_public_pages_and_auth_guards():
    with TestClient(app) as c:
        for path in ['/','/login','/register','/legal','/legal/terms','/legal/privacy','/legal/driver-agreement','/legal/prohibited-items','/legal/refunds','/market']:
            assert c.get(path).status_code == 200, path
        for path in ['/dashboard','/jobs','/orders','/connections']:
            assert c.get(path).status_code in (401,303), path

def test_every_role_and_core_workflows():
    with TestClient(app) as c:
        assert login(c,'owner@test.local','OwnerPass123!','admin').status_code==303; accept_legal(c)
        assert c.get('/dashboard').status_code==200; assert c.get('/shopping/ops').status_code==200; assert c.get('/admin/jobs').status_code==200; c.post('/logout')
        assert login(c,'safety@test.local','SafetyPass123!','safety').status_code==303; accept_legal(c); assert c.get('/dashboard').status_code==200
        assert c.post('/deliveries',data={'pickup':'A','dropoff':'B','item_description':'X','distance_miles':'1','notes':''}).status_code==403; c.post('/logout')
        assert login(c,'developer@test.local','DeveloperPass123!','developer').status_code==303; accept_legal(c); assert c.get('/dashboard').status_code==200; c.post('/logout')
        assert register(c,'customer@test.local','Customer123!','Test Customer','customer').status_code==303; c.post('/logout')
        assert register(c,'store@test.local','StorePass123!','Test Store','business','Test Store').status_code==303; c.post('/logout')
        assert register(c,'driver@test.local','DriverPass123!','Test Driver','driver').status_code==303
        assert c.get('/driver/shop').status_code==200; assert c.get('/driver/app').status_code==200
        assert c.get('/driver/offers.json').status_code==200; c.post('/logout')
        wrong=login(c,'driver@test.local','DriverPass123!','customer'); assert wrong.status_code==303 and '/login?wrong=1' in wrong.headers.get('location','')

def test_driver_pwa_notification_regression_20_cycles():
    with TestClient(app) as c:
        for i in range(20):
            m=c.get('/driver/manifest.webmanifest'); assert m.status_code==200 and m.json()['start_url']=='/driver/app'
            assert c.get('/driver/icon-192.png').status_code==200
            assert c.get('/driver/icon-512.png').status_code==200
            sw=c.get('/driver/sw.js'); assert sw.status_code==200 and 'notificationclick' in sw.text
            assert c.get('/driver/offers.json').status_code==401
        email='driver20@test.local'; password='Driver20Pass123!'
        r=register(c,email,password,'Twenty Cycle Driver','driver')
        if r.status_code!=303:
            assert login(c,email,password,'driver').status_code==303
        for i in range(20):
            page=c.get('/driver/app'); assert page.status_code==200
            assert 'Enable alerts' in page.text and '/driver/offers.json' in page.text and 'setInterval(checkOffers,15000)' in page.text
            offers=c.get('/driver/offers.json'); assert offers.status_code==200
            body=offers.json(); assert 'online' in body and 'offers' in body and isinstance(body['offers'],list)

def test_task_board_owner_lifecycle_and_safety():
    with TestClient(app) as c:
        assert login(c,'owner@test.local','OwnerPass123!','admin').status_code==303; accept_legal(c); assert c.get('/jobs').status_code==200
        r=c.post('/jobs',data={'title':'Help organize garage','description':'Move labeled storage boxes onto shelves for one hour.','category':'home','neighborhood':'Spokane','location_note':'General location shared after acceptance','offered_dollars':'40','timing_text':'Saturday afternoon','lawful_attestation':'yes'},follow_redirects=False); assert r.status_code==303
        assert c.get('/jobs?view=mine').status_code==200
        r=c.post('/jobs',data={'title':'Move ammunition','description':'Move ammunition boxes to another room safely.','category':'moving','neighborhood':'Spokane','location_note':'','offered_dollars':'40','timing_text':'','lawful_attestation':'yes'},follow_redirects=False); assert r.status_code==400
        assert c.post('/jobs/1/accept',follow_redirects=False).status_code==400
        assert c.post('/jobs/1/cancel',follow_redirects=False).status_code==303; assert c.get('/admin/jobs').status_code==200


def test_driver_web_app_shows_and_completes_community_tasks():
    from app.database import db
    title='Driver feed regression task'
    with TestClient(app) as c:
        assert login(c,'owner@test.local','OwnerPass123!','admin').status_code==303
        accept_legal(c)
        posted=c.post('/jobs',data={'title':title,'description':'Carry a few labeled boxes from the garage to the storage shelves.','category':'moving','neighborhood':'Spokane','location_note':'Meet at the public entrance','offered_dollars':'35','timing_text':'Today','lawful_attestation':'yes'},follow_redirects=False)
        assert posted.status_code==303
        with db() as con:
            task=con.execute('SELECT id FROM community_tasks WHERE title=?',(title,)).fetchone()
            assert task
            task_id=task['id']
        c.post('/logout')
        assert register(c,'driver-task-feed@test.local','DriverTaskFeed123!','Task Feed Driver','driver').status_code==303
        accept_legal(c)
        with db() as con:
            driver=con.execute('SELECT id FROM users WHERE email=?',('driver-task-feed@test.local',)).fetchone()
            driver_id=driver['id']
            con.execute('INSERT OR IGNORE INTO driver_profiles(user_id) VALUES(?)',(driver_id,))
            con.execute('UPDATE driver_profiles SET online=1 WHERE user_id=?',(driver_id,))
            con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(driver_id,'2026-10-09T00:00:00+00:00'))
            con.execute("UPDATE driver_compliance SET identity_status='approved',background_status='approved',insurance_status='approved' WHERE user_id=?",(driver_id,))
        page=c.get('/driver/app')
        assert page.status_code==200 and title in page.text
        offers=c.get('/driver/offers.json')
        assert offers.status_code==200
        assert any(o.get('key')==f'task:{task_id}' and o.get('type')=='task' for o in offers.json()['offers'])
        accepted=c.post(f'/driver/tasks/{task_id}/accept',follow_redirects=False)
        assert accepted.status_code==303 and '/driver/app#active' in accepted.headers.get('location','')
        active=c.get('/driver/app')
        assert active.status_code==200 and title in active.text and 'Task accepted' in active.text
        completed=c.post(f'/driver/tasks/{task_id}/complete',data={'note':'Moved boxes to the storage shelves.'},follow_redirects=False)
        assert completed.status_code==303 and '/driver/app#active' in completed.headers.get('location','')
        active=c.get('/driver/app')
        assert active.status_code==200 and 'Waiting for the customer to confirm completion.' in active.text
