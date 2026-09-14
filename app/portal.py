from __future__ import annotations
import os
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, db, now, hash_password, verify_password, sign, unsign, page, require_user, COOKIE_SECURE

OWNER_EMAIL=os.environ.get('LOCALLOOP_OWNER_EMAIL','').strip().lower()
OWNER_PASSWORD=os.environ.get('LOCALLOOP_OWNER_PASSWORD','')
SAFETY_EMAIL=os.environ.get('LOCALLOOP_SAFETY_EMAIL','').strip().lower()
SAFETY_PASSWORD=os.environ.get('LOCALLOOP_SAFETY_PASSWORD','')
DEVELOPER_EMAIL=os.environ.get('LOCALLOOP_DEVELOPER_EMAIL','').strip().lower()
DEVELOPER_PASSWORD=os.environ.get('LOCALLOOP_DEVELOPER_PASSWORD','')
DEMO_DRIVER_EMAIL=os.environ.get('LOCALLOOP_DEMO_DRIVER_EMAIL','').strip().lower()
DEMO_DRIVER_PASSWORD=os.environ.get('LOCALLOOP_DEMO_DRIVER_PASSWORD','')
DEMO_BUSINESS_EMAIL=os.environ.get('LOCALLOOP_DEMO_BUSINESS_EMAIL','').strip().lower()
DEMO_BUSINESS_PASSWORD=os.environ.get('LOCALLOOP_DEMO_BUSINESS_PASSWORD','')
DEMO_CUSTOMER_EMAIL=os.environ.get('LOCALLOOP_DEMO_CUSTOMER_EMAIL','').strip().lower()
DEMO_CUSTOMER_PASSWORD=os.environ.get('LOCALLOOP_DEMO_CUSTOMER_PASSWORD','')
TERMS_VERSION='2026-09-14'
PRIVACY_VERSION='2026-09-14'
DRIVER_VERSION='2026-09-14'

def staff_role_for(con,user_id:int):
    row=con.execute('SELECT staff_role FROM staff_access WHERE user_id=?',(user_id,)).fetchone(); return row['staff_role'] if row else None

def ensure_account(con,email,password,name,role,staff_role=None):
    if not email or not password: return
    row=con.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if row:
        uid=row['id']; con.execute('UPDATE users SET password_hash=?,name=?,role=?,verified=1,active=1 WHERE id=?',(hash_password(password),name,role,uid))
    else:
        cur=con.execute('INSERT INTO users(email,password_hash,name,role,verified,created_at) VALUES(?,?,?,?,1,?)',(email,hash_password(password),name,role,now())); uid=cur.lastrowid
    if staff_role: con.execute('INSERT OR REPLACE INTO staff_access(user_id,staff_role,read_only) VALUES(?,?,1)',(uid,staff_role))
    else: con.execute('DELETE FROM staff_access WHERE user_id=?',(uid,))
    if role=='driver': con.execute('INSERT OR IGNORE INTO driver_profiles(user_id) VALUES(?)',(uid,))
    if role=='business': con.execute('INSERT OR IGNORE INTO business_profiles(user_id,business_name) VALUES(?,?)',(uid,name))

def _has_current_legal(con,u):
    try:
        required=[('terms',TERMS_VERSION),('privacy',PRIVACY_VERSION)]
        if u['role']=='driver': required.append(('driver_agreement',DRIVER_VERSION))
        return all(con.execute('SELECT 1 FROM legal_acceptances WHERE user_id=? AND document_type=? AND version=?',(u['id'],doc,version)).fetchone() for doc,version in required)
    except Exception:
        return False

def _verified_for_use(con,u):
    if u['role'] not in {'customer','business'}: return True
    try:
        r=con.execute("SELECT verification_status FROM account_verifications WHERE user_id=?",(u['id'],)).fetchone()
        return bool(r and r['verification_status']=='verified')
    except Exception:
        return False

def _next_for_user(con,u):
    if not _has_current_legal(con,u): return '/legal/acceptance'
    if not _verified_for_use(con,u): return '/account/verification'
    return '/dashboard'

@app.on_event('startup')
def portal_startup():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS staff_access(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,staff_role TEXT NOT NULL CHECK(staff_role IN ('safety','developer')),read_only INTEGER DEFAULT 1)")
        ensure_account(con,OWNER_EMAIL,OWNER_PASSWORD,'LocalLoop Owner','admin')
        ensure_account(con,SAFETY_EMAIL,SAFETY_PASSWORD,'LocalLoop Safety Monitor','admin','safety')
        ensure_account(con,DEVELOPER_EMAIL,DEVELOPER_PASSWORD,'LocalLoop Developer','admin','developer')
        ensure_account(con,DEMO_DRIVER_EMAIL,DEMO_DRIVER_PASSWORD,'LocalLoop Demo Driver','driver')
        ensure_account(con,DEMO_BUSINESS_EMAIL,DEMO_BUSINESS_PASSWORD,'LocalLoop Demo Store','business')
        ensure_account(con,DEMO_CUSTOMER_EMAIL,DEMO_CUSTOMER_PASSWORD,'LocalLoop Demo Customer','customer')

app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in {'/login','/dashboard'}]

@app.middleware('http')
async def readonly_staff_guard(request:Request,call_next):
    allowed_staff_posts={'/login','/logout','/legal/acceptance'}
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path not in allowed_staff_posts:
        raw=request.cookies.get('ll_session'); uid=unsign(raw) if raw else None
        if uid and uid.isdigit():
            with db() as con:
                if con.execute('SELECT 1 FROM staff_access WHERE user_id=? AND read_only=1',(int(uid),)).fetchone(): raise HTTPException(403,'Safety and developer accounts are read-only.')
    return await call_next(request)

@app.get('/login',response_class=HTMLResponse)
def portal_login_page(request:Request): return page(request,'login.html')

@app.post('/login')
def portal_login(email:str=Form(...),password:str=Form(...),portal:str=Form('any')):
    with db() as con:
        u=con.execute('SELECT * FROM users WHERE email=?',(email.lower().strip(),)).fetchone(); staff=staff_role_for(con,u['id']) if u else None
        next_path=_next_for_user(con,u) if u else '/dashboard'
    if not u or not u['active'] or not verify_password(password,u['password_hash']): raise HTTPException(400,'Invalid login')
    actual=staff or u['role']; expected={'store':'business','owner':'admin'}.get(portal,portal)
    if expected not in {'any',actual}: raise HTTPException(403,f'This account belongs to the {actual} portal.')
    with db() as con:
        try: con.execute('UPDATE users SET last_login_at=?,login_count=COALESCE(login_count,0)+1 WHERE id=?',(now(),u['id']))
        except Exception: pass
    r=RedirectResponse(next_path,303); r.set_cookie('ll_session',sign(str(u['id'])),httponly=True,samesite='lax',secure=COOKIE_SECURE); return r

@app.get('/dashboard',response_class=HTMLResponse)
def portal_dashboard(request:Request):
    u=require_user(request)
    with db() as con:
        next_path=_next_for_user(con,u)
        if next_path!='/dashboard': return RedirectResponse(next_path,303)
        staff=staff_role_for(con,u['id'])
        if staff:
            stats={'users':con.execute('SELECT COUNT(*) c FROM users').fetchone()['c'],'online':con.execute('SELECT COUNT(*) c FROM driver_profiles WHERE online=1').fetchone()['c'],'active':con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()['c'],'today':con.execute("SELECT COUNT(*) c FROM deliveries WHERE date(created_at)=date('now')").fetchone()['c'],'disputes':con.execute("SELECT COUNT(*) c FROM disputes WHERE status='open'").fetchone()['c']}; rows=con.execute('SELECT d.*,u.name customer,dr.name driver FROM deliveries d JOIN users u ON u.id=d.customer_id LEFT JOIN users dr ON dr.id=d.driver_id ORDER BY d.id DESC LIMIT 40').fetchall(); return page(request,'staff.html',staff_role=staff,stats=stats,deliveries=rows)
        if u['role']=='admin':
            stats={'users':con.execute('SELECT COUNT(*) c FROM users').fetchone()['c'],'online':con.execute('SELECT COUNT(*) c FROM driver_profiles WHERE online=1').fetchone()['c'],'active':con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()['c'],'today':con.execute("SELECT COUNT(*) c FROM deliveries WHERE date(created_at)=date('now')").fetchone()['c'],'revenue':con.execute("SELECT COALESCE(SUM(platform_fee_cents),0) c FROM deliveries WHERE status='delivered'").fetchone()['c'],'disputes':con.execute("SELECT COUNT(*) c FROM disputes WHERE status='open'").fetchone()['c']}; rows=con.execute('SELECT d.*,u.name customer,dr.name driver FROM deliveries d JOIN users u ON u.id=d.customer_id LEFT JOIN users dr ON dr.id=d.driver_id ORDER BY d.id DESC LIMIT 50').fetchall(); return page(request,'admin.html',stats=stats,deliveries=rows)
        if u['role']=='driver':
            prof=con.execute('SELECT * FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone(); available=con.execute("SELECT d.*,u.name customer FROM deliveries d JOIN users u ON u.id=d.customer_id WHERE d.status='posted' ORDER BY d.id DESC").fetchall(); mine=con.execute('SELECT * FROM deliveries WHERE driver_id=? ORDER BY id DESC LIMIT 30',(u['id'],)).fetchall(); return page(request,'driver.html',profile=prof,available=available,mine=mine)
        mine=con.execute('SELECT d.*,dr.name driver FROM deliveries d LEFT JOIN users dr ON dr.id=d.driver_id WHERE d.customer_id=? OR d.business_id=? ORDER BY d.id DESC',(u['id'],u['id'])).fetchall(); return page(request,'customer.html',deliveries=mine,account_verified=True)

from . import main as _main_module
from .database import db as _persistent_db
_main_module.db=_persistent_db
db=_persistent_db

from . import marketplace  # noqa: E402,F401
from . import production  # noqa: E402,F401
from . import payments  # noqa: E402,F401
from . import routing  # noqa: E402,F401
from . import legal  # noqa: E402,F401
from . import community_marketplace  # noqa: E402,F401
from . import marketplace_payments  # noqa: E402,F401
from . import admin_accounts  # noqa: E402,F401
from . import ux  # noqa: E402,F401
