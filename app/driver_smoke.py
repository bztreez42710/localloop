from __future__ import annotations
import asyncio, os, time
import httpx
from .main import app, hash_password, now
from .database import db

BASE=os.environ.get('LOCALLOOP_PUBLIC_URL','https://localloop-app.onrender.com').rstrip('/')
EMAIL=os.environ.get('LOCALLOOP_DEMO_DRIVER_EMAIL','').strip()
PASSWORD=os.environ.get('LOCALLOOP_DEMO_DRIVER_PASSWORD','')

async def _smoke():
    await asyncio.sleep(15)
    if not EMAIL or not PASSWORD:
        print('DRIVER_SMOKE_SKIPPED no demo credentials',flush=True); return
    did=None; driver_id=None; original_profile=None
    try:
        with db() as con:
            duser=con.execute('SELECT * FROM users WHERE email=?',(EMAIL.lower(),)).fetchone()
            if not duser: raise RuntimeError('demo driver account missing')
            driver_id=duser['id']
            con.execute('INSERT OR IGNORE INTO driver_profiles(user_id) VALUES(?)',(driver_id,))
            p=con.execute('SELECT completed,payout_balance_cents,online,latitude,longitude FROM driver_profiles WHERE user_id=?',(driver_id,)).fetchone()
            original_profile=dict(p) if p else {'completed':0,'payout_balance_cents':0,'online':0,'latitude':None,'longitude':None}
            con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(driver_id,now()))
            con.execute("UPDATE driver_compliance SET identity_status='approved',background_status='approved',insurance_status='approved',background_consent=1,insurance_company='Smoke Test Carrier',insurance_policy_last4='1234',insurance_expires='2030-12-31',payout_email=?,updated_at=? WHERE user_id=?",(EMAIL.lower(),now(),driver_id))
            smoke_email='driver-smoke-customer@local.test'
            customer=con.execute('SELECT id FROM users WHERE email=?',(smoke_email,)).fetchone()
            if not customer:
                cur=con.execute("INSERT INTO users(email,password_hash,name,role,verified,created_at) VALUES(?,?,?,?,1,?)",(smoke_email,hash_password('SmokeOnly-NotForLogin-123!'),'Driver Smoke Customer','customer',now())); customer_id=cur.lastrowid
            else: customer_id=customer['id']
            marker=f'DRIVER_SMOKE_{int(time.time())}'
            cur=con.execute("INSERT INTO deliveries(customer_id,pickup,dropoff,item_description,distance_miles,quoted_cents,platform_fee_cents,driver_pay_cents,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(customer_id,'100 N Howard St, Spokane, WA 99201','808 W Main Ave, Spokane, WA 99201',marker,1.0,770,150,620,'posted','automated test',now(),now())); did=cur.lastrowid

        async with httpx.AsyncClient(base_url=BASE,follow_redirects=False,timeout=25.0) as c:
            r=await c.get('/driver/app')
            if r.status_code not in (303,307) or '/driver/login' not in r.headers.get('location',''): raise RuntimeError(f'guest app guard {r.status_code} {r.headers.get("location","")}')
            for path in ['/driver/manifest.webmanifest','/driver/icon.svg','/driver/icon-192.png','/driver/icon-512.png','/driver/sw.js']:
                r=await c.get(path)
                if r.status_code!=200: raise RuntimeError(f'{path} returned {r.status_code}')
            r=await c.get('/driver/manifest.webmanifest')
            if 'icon-192.png' not in r.text or 'icon-512.png' not in r.text: raise RuntimeError('PWA manifest missing PNG install icons')
            r=await c.post('/login',data={'email':EMAIL,'password':PASSWORD,'portal':'driver','next':'/driver/app'})
            if r.status_code not in (303,307): raise RuntimeError(f'login returned {r.status_code}')
            if r.headers.get('location','')=='/legal/acceptance':
                r=await c.post('/legal/acceptance',data={'accept_terms':'yes','accept_privacy':'yes','accept_driver':'yes'})
                if r.status_code not in (303,307): raise RuntimeError(f'legal acceptance returned {r.status_code}')
            r=await c.get('/dashboard')
            if r.status_code not in (303,307) or '/driver/app' not in r.headers.get('location',''): raise RuntimeError(f'driver dashboard routing {r.status_code} {r.headers.get("location","")}')
            r=await c.get('/driver/app')
            if r.status_code!=200 or 'LOCALLOOP DRIVER' not in r.text.upper(): raise RuntimeError(f'driver app initial render {r.status_code}')
            r=await c.get('/driver/setup')
            if r.status_code!=200: raise RuntimeError(f'driver setup GET {r.status_code}')
            r=await c.post('/driver/setup',data={'background_consent':'1','insurance_company':'Smoke Test Carrier','insurance_policy_last4':'1234','insurance_expires':'2030-12-31','payout_email':EMAIL})
            if r.status_code not in (303,307) or '/driver/verify/status' not in r.headers.get('location',''): raise RuntimeError(f'driver setup POST {r.status_code} {r.headers.get("location","")}')
            r=await c.get('/driver/verify/status?setup_saved=1')
            if r.status_code!=200 or 'Return to Driver App' not in r.text: raise RuntimeError(f'verification status {r.status_code}')
            r=await c.post('/driver/location',data={'latitude':'47.6588','longitude':'-117.4260'})
            if r.status_code!=200: raise RuntimeError(f'valid location {r.status_code}')
            r=await c.post('/driver/location',data={'latitude':'0','longitude':'0'})
            if r.status_code!=400: raise RuntimeError(f'invalid location should be 400, got {r.status_code}')
            r=await c.post('/driver/online',data={'online':'1'})
            if r.status_code not in (303,307) or '/driver/app' not in r.headers.get('location',''): raise RuntimeError(f'go online {r.status_code} {r.headers.get("location","")}')
            r=await c.get('/driver/app')
            if r.status_code!=200 or f'Delivery #{did}' not in r.text: raise RuntimeError(f'offer not visible {r.status_code}')
            r=await c.post(f'/deliveries/{did}/accept')
            if r.status_code not in (303,307) or '/driver/app' not in r.headers.get('location',''): raise RuntimeError(f'accept {r.status_code} {r.headers.get("location","")}')
            r=await c.get('/driver/app')
            if r.status_code!=200 or f'Delivery #{did}' not in r.text or 'Driver accepted' not in r.text: raise RuntimeError(f'active delivery render {r.status_code}')
            r=await c.get(f'/api/track/{did}')
            if r.status_code!=200: raise RuntimeError(f'tracking API {r.status_code}')
            tracking=r.json(); loc=tracking.get('driver') or {}
            if round(float(loc.get('latitude',0)),4)!=47.6588 or round(float(loc.get('longitude',0)),4)!=-117.4260: raise RuntimeError(f'tracking coordinates wrong {tracking}')
            r=await c.get(f'/track/{did}')
            if r.status_code!=200: raise RuntimeError(f'tracking page {r.status_code}')
            r=await c.get(f'/navigate/{did}')
            if r.status_code!=200: raise RuntimeError(f'navigation page {r.status_code}')
            r=await c.post(f'/deliveries/{did}/status',data={'status':'picked_up'})
            loc=r.headers.get('location','')
            if r.status_code not in (303,307) or '/driver/app' not in loc: raise RuntimeError(f'pickup transition {r.status_code} {loc}')
            r=await c.get('/driver/app')
            if r.status_code!=200 or 'Picked up' not in r.text: raise RuntimeError(f'picked-up render {r.status_code}')
            r=await c.post(f'/deliveries/{did}/status',data={'status':'delivered','proof':'Smoke test delivery'})
            loc=r.headers.get('location','')
            if r.status_code not in (303,307) or '/driver/app' not in loc: raise RuntimeError(f'delivery transition {r.status_code} {loc}')
            r=await c.get('/orders')
            if r.status_code!=200: raise RuntimeError(f'orders/earnings page {r.status_code}')
            r=await c.post('/driver/online',data={'online':'0'})
            if r.status_code not in (303,307) or '/driver/app' not in r.headers.get('location',''): raise RuntimeError(f'go offline {r.status_code} {r.headers.get("location","")}')
            print('DRIVER_SMOKE_OK guest_guard install_manifest png_icons login dashboard_redirect app setup verification gps online offer accept active live_tracking tracking_page navigate pickup deliver earnings offline',flush=True)
    except Exception as e:
        print(f'DRIVER_SMOKE_FAIL {type(e).__name__}: {e}',flush=True)
    finally:
        try:
            if did:
                with db() as con:
                    con.execute('DELETE FROM ledger WHERE delivery_id=?',(did,))
                    con.execute('DELETE FROM delivery_events WHERE delivery_id=?',(did,))
                    con.execute('DELETE FROM disputes WHERE delivery_id=?',(did,))
                    con.execute('DELETE FROM ratings WHERE delivery_id=?',(did,))
                    con.execute('DELETE FROM payment_records WHERE delivery_id=?',(did,))
                    con.execute('DELETE FROM deliveries WHERE id=?',(did,))
                    if driver_id and original_profile:
                        con.execute('UPDATE driver_profiles SET completed=?,payout_balance_cents=?,online=?,latitude=?,longitude=? WHERE user_id=?',(original_profile['completed'],original_profile['payout_balance_cents'],original_profile['online'],original_profile['latitude'],original_profile['longitude'],driver_id))
        except Exception as cleanup_error:
            print(f'DRIVER_SMOKE_CLEANUP_FAIL {cleanup_error}',flush=True)

@app.on_event('startup')
async def driver_smoke_startup():
    asyncio.create_task(_smoke())
