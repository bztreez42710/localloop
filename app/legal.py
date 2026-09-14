from __future__ import annotations
import sqlite3
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, now, page, hash_password, sign, COOKIE_SECURE
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

# Replace the original registration routes so acceptance is recorded.
app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/register']

@app.get('/register', response_class=HTMLResponse)
def legal_register_page(request:Request): return page(request,'register.html')

@app.post('/register')
def legal_register(request:Request,email:str=Form(...),password:str=Form(...),name:str=Form(...),role:str=Form(...),business_name:str=Form(''),accept_terms:str=Form(''),accept_driver:str=Form('')):
    if role not in {'customer','driver','business'}: raise HTTPException(400,'Bad role')
    if accept_terms!='yes': raise HTTPException(400,'You must accept the Terms of Use and Privacy Policy to create an account.')
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
    r=RedirectResponse('/dashboard',303); r.set_cookie('ll_session',sign(str(uid)),httponly=True,samesite='lax',secure=COOKIE_SECURE); return r
