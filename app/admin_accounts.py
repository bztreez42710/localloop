from __future__ import annotations
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, page, require_user, unsign, now
from .database import db

@app.on_event('startup')
def account_admin_startup():
    with db() as con:
        for col in ["last_login_at TEXT DEFAULT ''","last_seen_at TEXT DEFAULT ''","login_count INTEGER DEFAULT 0","admin_note TEXT DEFAULT ''"]:
            try: con.execute(f'ALTER TABLE users ADD COLUMN {col}')
            except Exception: pass
        con.execute('''CREATE TABLE IF NOT EXISTS account_admin_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            actor_id INTEGER REFERENCES users(id),event TEXT NOT NULL,note TEXT DEFAULT '',created_at TEXT NOT NULL)''')

@app.middleware('http')
async def account_access_guard(request:Request, call_next):
    raw=request.cookies.get('ll_session'); payload=unsign(raw) if raw else None
    uid=None
    if payload:
        first=payload.split(':',1)[0]
        if first.isdigit(): uid=int(first)
    if uid:
        with db() as con:
            u=con.execute('SELECT id,active FROM users WHERE id=?',(uid,)).fetchone()
            if not u or not u['active']:
                r=RedirectResponse('/login?access=disabled',303); r.delete_cookie('ll_session'); return r
            try: con.execute('UPDATE users SET last_seen_at=? WHERE id=?',(now(),uid))
            except Exception: pass
    return await call_next(request)

def owner(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        staff=con.execute('SELECT staff_role FROM staff_access WHERE user_id=?',(u['id'],)).fetchone()
        if staff: raise HTTPException(403,'Owner access required.')
    return u

@app.get('/admin/accounts',response_class=HTMLResponse)
def admin_accounts(request:Request):
    owner(request)
    with db() as con:
        users=con.execute('''SELECT u.*,sa.staff_role,
          (SELECT COUNT(*) FROM legal_acceptances la WHERE la.user_id=u.id) legal_count,
          (SELECT verification_status FROM account_verifications av WHERE av.user_id=u.id) verification_status
          FROM users u LEFT JOIN staff_access sa ON sa.user_id=u.id ORDER BY u.id DESC''').fetchall()
    return page(request,'admin_accounts.html',users=users)

@app.get('/admin/accounts/{uid}',response_class=HTMLResponse)
def admin_account_detail(uid:int,request:Request):
    owner(request)
    with db() as con:
        u=con.execute('SELECT u.*,sa.staff_role FROM users u LEFT JOIN staff_access sa ON sa.user_id=u.id WHERE u.id=?',(uid,)).fetchone()
        if not u: raise HTTPException(404)
        legal=con.execute('SELECT document_type,version,accepted_at,ip_note FROM legal_acceptances WHERE user_id=? ORDER BY id DESC',(uid,)).fetchall()
        try: verification=con.execute('SELECT * FROM account_verifications WHERE user_id=?',(uid,)).fetchone()
        except Exception: verification=None
        deliveries=con.execute('SELECT id,status,pickup,dropoff,created_at FROM deliveries WHERE customer_id=? OR driver_id=? ORDER BY id DESC LIMIT 25',(uid,uid)).fetchall()
        events=con.execute('SELECT * FROM account_admin_events WHERE user_id=? ORDER BY id DESC LIMIT 25',(uid,)).fetchall()
    return page(request,'admin_account_detail.html',account=u,legal=legal,verification=verification,deliveries=deliveries,events=events)

@app.post('/admin/accounts/{uid}/access')
def admin_account_access(uid:int,request:Request,active:int=Form(...),note:str=Form('')):
    admin=owner(request)
    if uid==admin['id'] and not active: raise HTTPException(400,'You cannot disable your own owner account.')
    with db() as con:
        target=con.execute('SELECT id,role FROM users WHERE id=?',(uid,)).fetchone()
        if not target: raise HTTPException(404)
        con.execute('UPDATE users SET active=?,admin_note=? WHERE id=?',(1 if active else 0,note.strip()[:1000],uid))
        con.execute('INSERT INTO account_admin_events(user_id,actor_id,event,note,created_at) VALUES(?,?,?,?,?)',(uid,admin['id'],'login_enabled' if active else 'login_disabled',note.strip()[:1000],now()))
    return RedirectResponse(f'/admin/accounts/{uid}',303)

@app.post('/admin/accounts/{uid}/verify')
def admin_account_verify(uid:int,request:Request,verified:int=Form(...),note:str=Form('')):
    admin=owner(request)
    with db() as con:
        target=con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not target: raise HTTPException(404)
        con.execute('UPDATE users SET verified=? WHERE id=?',(1 if verified else 0,uid))
        con.execute('INSERT INTO account_admin_events(user_id,actor_id,event,note,created_at) VALUES(?,?,?,?,?)',(uid,admin['id'],'account_verified' if verified else 'account_unverified',note.strip()[:1000],now()))
    return RedirectResponse(f'/admin/accounts/{uid}',303)
