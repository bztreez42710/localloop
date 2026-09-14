from __future__ import annotations
import os, time, secrets, hashlib, hmac
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import httpx
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from .main import app, page, now, hash_password
from .database import db

PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL','https://localloop-app.onrender.com').rstrip('/')
RESEND_API_KEY=os.environ.get('RESEND_API_KEY','').strip()
RESEND_FROM_EMAIL=os.environ.get('RESEND_FROM_EMAIL','').strip()
RESET_TTL_MINUTES=30
_RATE=defaultdict(deque)

def _ip(request:Request)->str:
    forwarded=(request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
    return forwarded or (request.client.host if request.client else 'unknown')

def _limited(key:str,limit:int,window:int)->bool:
    t=time.time(); q=_RATE[key]
    while q and q[0] < t-window: q.popleft()
    if len(q)>=limit: return True
    q.append(t); return False

def _same_origin(request:Request)->bool:
    host=(request.headers.get('host') or '').lower()
    origin=(request.headers.get('origin') or '').lower()
    referer=(request.headers.get('referer') or '').lower()
    if origin:
        return origin.startswith('https://'+host) or origin.startswith('http://'+host)
    if referer:
        return referer.startswith('https://'+host+'/') or referer.startswith('http://'+host+'/')
    return True

@app.middleware('http')
async def security_guard(request:Request,call_next):
    path=request.url.path
    if path in {'/login','/register','/forgot-password'} and request.method=='POST':
        limit=10 if path=='/login' else 5
        window=600 if path=='/login' else 3600
        if _limited(f'{path}:{_ip(request)}',limit,window):
            return JSONResponse({'detail':'Too many attempts. Please wait and try again.'},status_code=429)
    elif request.method not in {'GET','HEAD','OPTIONS'}:
        if _limited(f'post:{_ip(request)}',120,60):
            return JSONResponse({'detail':'Too many requests. Please slow down.'},status_code=429)
        if not _same_origin(request):
            return JSONResponse({'detail':'Request blocked by LocalLoop security protection.'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(self), geolocation=(self), microphone=()'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline' https://unpkg.com; script-src 'self' 'unsafe-inline' https://js.finix.com https://unpkg.com; connect-src 'self' https://*.finix.com https://*.payments-api.com https://nominatim.openstreetmap.org https://router.project-osrm.org; frame-src https://js.finix.com https://*.finix.com; font-src 'self' data:; base-uri 'self'; form-action 'self'"
    if request.url.scheme=='https' or request.headers.get('x-forwarded-proto')=='https':
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    if path.startswith(('/login','/register','/forgot-password','/reset-password','/admin')):
        response.headers['Cache-Control']='no-store'
    return response

@app.on_event('startup')
def security_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS password_reset_tokens(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id,expires_at)')

def _send_reset(email:str,link:str)->bool:
    if not (RESEND_API_KEY and RESEND_FROM_EMAIL): return False
    try:
        r=httpx.post('https://api.resend.com/emails',headers={'Authorization':f'Bearer {RESEND_API_KEY}','Content-Type':'application/json'},json={
            'from':RESEND_FROM_EMAIL,'to':[email],'subject':'Reset your LocalLoop password',
            'text':f'Use this link to reset your LocalLoop password. It expires in {RESET_TTL_MINUTES} minutes: {link}'
        },timeout=20)
        return r.status_code<300
    except Exception:
        return False

@app.get('/forgot-password',response_class=HTMLResponse)
def forgot_password_page(request:Request):
    return page(request,'forgot_password.html',sent=False)

@app.post('/forgot-password',response_class=HTMLResponse)
def forgot_password(request:Request,email:str=Form(...)):
    email=email.lower().strip(); raw=secrets.token_urlsafe(32); token_hash=hashlib.sha256(raw.encode()).hexdigest()
    expires=(datetime.now(timezone.utc)+timedelta(minutes=RESET_TTL_MINUTES)).isoformat()
    with db() as con:
        u=con.execute('SELECT id,email FROM users WHERE email=? AND active=1',(email,)).fetchone()
        if u:
            con.execute('UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL',(now(),u['id']))
            con.execute('INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)',(u['id'],token_hash,expires,now()))
            _send_reset(u['email'],f'{PUBLIC_BASE_URL}/reset-password?token={raw}')
    return page(request,'forgot_password.html',sent=True)

@app.get('/reset-password',response_class=HTMLResponse)
def reset_password_page(request:Request,token:str=''):
    return page(request,'reset_password.html',token=token)

@app.post('/reset-password')
def reset_password(token:str=Form(...),password:str=Form(...),password2:str=Form(...)):
    if len(password)<10: raise HTTPException(400,'Use at least 10 characters for your new password.')
    if password!=password2: raise HTTPException(400,'Passwords do not match.')
    token_hash=hashlib.sha256(token.encode()).hexdigest(); t=now()
    with db() as con:
        row=con.execute('SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL',(token_hash,)).fetchone()
        if not row or row['expires_at']<t: raise HTTPException(400,'This reset link is invalid or expired.')
        con.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(password),row['user_id']))
        con.execute('UPDATE password_reset_tokens SET used_at=? WHERE id=?',(t,row['id']))
    return RedirectResponse('/login?reset=1',303)
