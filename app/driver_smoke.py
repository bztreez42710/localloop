from __future__ import annotations
import asyncio, os
import httpx
from .main import app

BASE=os.environ.get('LOCALLOOP_PUBLIC_URL','https://localloop-app.onrender.com').rstrip('/')
EMAIL=os.environ.get('LOCALLOOP_DEMO_DRIVER_EMAIL','').strip()
PASSWORD=os.environ.get('LOCALLOOP_DEMO_DRIVER_PASSWORD','')

async def _smoke():
    await asyncio.sleep(3)
    if not EMAIL or not PASSWORD:
        print('DRIVER_SMOKE_SKIPPED no demo credentials',flush=True); return
    try:
        async with httpx.AsyncClient(base_url=BASE,follow_redirects=False,timeout=20.0) as c:
            r=await c.get('/driver/app')
            if r.status_code not in (303,307) or '/driver/login' not in r.headers.get('location',''):
                raise RuntimeError(f'guest driver/app expected login redirect, got {r.status_code} {r.headers.get("location","")}')
            r=await c.post('/login',data={'email':EMAIL,'password':PASSWORD,'portal':'driver','next':'/driver/app'})
            if r.status_code not in (303,307): raise RuntimeError(f'login returned {r.status_code}')
            loc=r.headers.get('location','')
            if loc=='/legal/acceptance':
                r=await c.post('/legal/acceptance',data={'accept_terms':'yes','accept_privacy':'yes','accept_driver':'yes'})
                if r.status_code not in (303,307): raise RuntimeError(f'legal acceptance returned {r.status_code}')
            r=await c.get('/driver/app')
            if r.status_code!=200 or 'LOCALLOOP DRIVER' not in r.text.upper(): raise RuntimeError(f'driver app returned {r.status_code}')
            r=await c.post('/driver/setup',data={'background_consent':'1','insurance_company':'Test Carrier','insurance_policy_last4':'1234','insurance_expires':'2030-12-31','payout_email':EMAIL})
            if r.status_code not in (303,307) or not r.headers.get('location','').startswith('/driver/verify/status'):
                raise RuntimeError(f'driver setup did not continue: {r.status_code} {r.headers.get("location","")}')
            r=await c.get('/driver/verify/status?setup_saved=1')
            if r.status_code!=200: raise RuntimeError(f'verification status returned {r.status_code}')
            print('DRIVER_SMOKE_OK login driver_app_200 setup_saved continue_to_verification_200',flush=True)
    except Exception as e:
        print(f'DRIVER_SMOKE_FAIL {type(e).__name__}: {e}',flush=True)

@app.on_event('startup')
async def driver_smoke_startup():
    asyncio.create_task(_smoke())
