from __future__ import annotations
import sqlite3
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, now, page, hash_password, sign, COOKIE_SECURE, require_user
from .database import db

TERMS_VERSION='2026-09-14'
PRIVACY_VERSION='2026-09-14'
DRIVER_VERSION='2026-09-14'

@app.on_event('startup')
def legal_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS legal_acceptances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            document_type TEXT NOT NULL,
            version TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            ip_note TEXT DEFAULT '',
            UNIQUE(user_id,document_type,version)
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS account_verifications(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            legal_name TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            age_18_confirmed INTEGER NOT NULL DEFAULT 0,
            truthful_info_confirmed INTEGER NOT NULL DEFAULT 0,
            prohibited_items_ack INTEGER NOT NULL DEFAULT 0,
            refund_policy_ack INTEGER NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'pending',
            verified_at TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )''')

def _has_acceptance(con,uid:int,doc:str,version:str)->bool:
    return bool(con.execute('SELECT 1 FROM legal_acceptances WHERE user_id=? AND document_type=? AND version=?',(uid,doc,version)).fetchone())

def legal_status(con,u):
    terms=_has_acceptance(con,u['id'],'terms',TERMS_VERSION)
    privacy=_has_acceptance(con,u['id'],'privacy',PRIVACY_VERSION)
    driver=True if u['role']!='driver' else _has_acceptance(con,u['id'],'driver_agreement',DRIVER_VERSION)
    return {'terms':terms,'privacy':privacy,'driver':driver,'complete':terms and privacy and driver}

def verification_complete(con,uid:int)->bool:
    r=con.execute("SELECT verification_status FROM account_verifications WHERE user_id=?",(uid,)).fetchone()
    return bool(r and r['verification_status']=='verified')

@app.get('/legal', response_class=HTMLResponse)
def legal_center(request:Request):
    return page(request,'legal_center.html')

@app.get('/legal/terms', response_class=HTMLResponse)
def legal_terms(request:Request): return page(request,'legal_terms.html',version=TERMS_VERSION)
@app.get('/legal/privacy', response_class=HTMLResponse)
def legal_privacy(request:Request): return page(request,'legal_privacy.html',version=PRIVACY_VERSION)
@app.get('/legal/driver-agreement', response_class=HTMLResponse)
def legal_driver(request:Request): return page(request,'legal_driver.html',version=DRIVER_VERSION)
@app.get('/legal/prohibited-items', response_class=HTMLResponse)
def legal_prohibited(request:Request): return page(request,'legal_prohibited.html')
@app.get('/legal/refunds', response_class=HTMLResponse)
def legal_refunds(request:Request): return page(request,'legal_refunds.html')

@app.get('/legal/acceptance',response_class=HTMLResponse)
def acceptance_page(request:Request):
    u=require_user(request)
    with db() as con: status=legal_status(con,u)
    if status['complete']: return RedirectResponse('/account/verification' if u['role'] in {'customer','business'} else '/dashboard',303)
    return page(request,'legal_acceptance.html',status=status,terms_version=TERMS_VERSION,privacy_version=PRIVACY_VERSION,driver_version=DRIVER_VERSION)

@app.post('/legal/acceptance')
def acceptance_save(request:Request,accept_terms:str=Form(''),accept_privacy:str=Form(''),accept_driver:str=Form('')):
    u=require_user(request)
    if accept_terms!='yes' or accept_privacy!='yes': raise HTTPException(400,'You must accept the current Terms of Use and acknowledge the Privacy Policy before using LocalLoop.')
    if u['role']=='driver' and accept_driver!='yes': raise HTTPException(400,'Independent drivers must accept the current Independent Shopper & Driver Agreement.')
    ip=(request.client.host if request.client else '')[:64]
    with db() as con:
        con.execute('INSERT OR IGNORE INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(u['id'],'terms',TERMS_VERSION,now(),ip))
        con.execute('INSERT OR IGNORE INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(u['id'],'privacy',PRIVACY_VERSION,now(),ip))
        if u['role']=='driver': con.execute('INSERT OR IGNORE INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(u['id'],'driver_agreement',DRIVER_VERSION,now(),ip))
    return RedirectResponse('/account/verification' if u['role'] in {'customer','business'} else '/dashboard',303)

@app.get('/account/verification',response_class=HTMLResponse)
def account_verification_page(request:Request):
    u=require_user(request)
    with db() as con:
        status=legal_status(con,u)
        if not status['complete']: return RedirectResponse('/legal/acceptance',303)
        row=con.execute('SELECT * FROM account_verifications WHERE user_id=?',(u['id'],)).fetchone()
    if u['role'] not in {'customer','business'}: return RedirectResponse('/dashboard',303)
    if row and row['verification_status']=='verified': return RedirectResponse('/dashboard',303)
    return page(request,'account_verification.html',verification=row)

@app.post('/account/verification')
def account_verification_save(request:Request,legal_name:str=Form(...),phone:str=Form(...),age_18:str=Form(''),truthful_info:str=Form(''),prohibited_ack:str=Form(''),refund_ack:str=Form('')):
    u=require_user(request)
    if u['role'] not in {'customer','business'}: raise HTTPException(403)
    with db() as con:
        if not legal_status(con,u)['complete']: return RedirectResponse('/legal/acceptance',303)
    legal_name=legal_name.strip(); phone=phone.strip()
    digits=''.join(c for c in phone if c.isdigit())
    if len(legal_name)<2: raise HTTPException(400,'Enter your legal name.')
    if len(digits)<10: raise HTTPException(400,'Enter a valid phone number.')
    if age_18!='yes': raise HTTPException(400,'LocalLoop accounts used to buy, sell, or request delivery must be operated by an adult age 18 or older during this pilot.')
    if truthful_info!='yes' or prohibited_ack!='yes' or refund_ack!='yes': raise HTTPException(400,'Complete all verification acknowledgements before continuing.')
    t=now()
    with db() as con:
        con.execute('''INSERT OR REPLACE INTO account_verifications(user_id,legal_name,phone,age_18_confirmed,truthful_info_confirmed,prohibited_items_ack,refund_policy_ack,verification_status,verified_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',(u['id'],legal_name,phone,1,1,1,1,'verified',t,t))
        con.execute('UPDATE users SET phone=?,verified=1 WHERE id=?',(phone,u['id']))
    return RedirectResponse('/dashboard',303)

# Replace the original registration routes so acceptance is recorded.
app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/register']

@app.get('/register', response_class=HTMLResponse)
def legal_register_page(request:Request): return page(request,'register.html',terms_version=TERMS_VERSION,privacy_version=PRIVACY_VERSION,driver_version=DRIVER_VERSION)

@app.post('/register')
def legal_register(request:Request,email:str=Form(...),password:str=Form(...),name:str=Form(...),role:str=Form(...),business_name:str=Form(''),accept_terms:str=Form(''),accept_privacy:str=Form(''),accept_driver:str=Form(''),age_18:str=Form('')):
    if role not in {'customer','driver','business'}: raise HTTPException(400,'Bad role')
    if age_18!='yes': raise HTTPException(400,'You must confirm you are at least 18 to create an account during the LocalLoop pilot.')
    if accept_terms!='yes' or accept_privacy!='yes': raise HTTPException(400,'You must accept the Terms of Use and acknowledge the Privacy Policy to create an account.')
    if role=='driver' and accept_driver!='yes': raise HTTPException(400,'Independent drivers must accept the Driver Agreement.')
    try:
        with db() as con:
            cur=con.execute('INSERT INTO users(email,password_hash,name,role,created_at) VALUES(?,?,?,?,?)',(email.lower().strip(),hash_password(password),name.strip(),role,now())); uid=cur.lastrowid
            if role=='driver': con.execute('INSERT INTO driver_profiles(user_id) VALUES(?)',(uid,))
            if role=='business': con.execute('INSERT INTO business_profiles(user_id,business_name) VALUES(?,?)',(uid,business_name.strip() or name.strip()))
            ip=(request.client.host if request.client else '')[:64]
            con.execute('INSERT INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(uid,'terms',TERMS_VERSION,now(),ip))
            con.execute('INSERT INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(uid,'privacy',PRIVACY_VERSION,now(),ip))
            if role=='driver': con.execute('INSERT INTO legal_acceptances(user_id,document_type,version,accepted_at,ip_note) VALUES(?,?,?,?,?)',(uid,'driver_agreement',DRIVER_VERSION,now(),ip))
    except sqlite3.IntegrityError: raise HTTPException(400,'Email already exists')
    r=RedirectResponse('/account/verification' if role in {'customer','business'} else '/dashboard',303); r.set_cookie('ll_session',sign(str(uid)),httponly=True,samesite='lax',secure=COOKIE_SECURE); return r
