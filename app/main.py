from __future__ import annotations
import os, sqlite3, hashlib, hmac, secrets, json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("LOCALLOOP_DB", str(BASE.parent / "localloop.db")))
SECRET = os.environ.get("LOCALLOOP_SECRET", "dev-secret-change-me")
ADMIN_EMAIL = os.environ.get("LOCALLOOP_ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("LOCALLOOP_ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("LOCALLOOP_COOKIE_SECURE", "0") == "1"
PLATFORM_FEE = int(os.environ.get("PLATFORM_FEE_CENTS", "150"))
BASE_FARE = int(os.environ.get("BASE_FARE_CENTS", "500"))
PER_MILE = int(os.environ.get("PER_MILE_CENTS", "120"))
SURGE = float(os.environ.get("SURGE_MULTIPLIER", "1.0"))

app = FastAPI(title="LocalLoop", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

@app.middleware('http')
async def supabase_session_refresh(request:Request,call_next):
    response=await call_next(request)
    refreshed=getattr(request.state,'supabase_refreshed',None)
    if refreshed:
        from .supabase_auth import set_auth_cookies
        set_auth_cookies(response,refreshed,COOKIE_SECURE)
    return response

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def now(): return datetime.now(timezone.utc).isoformat()
def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex()+":"+digest.hex()
def verify_password(password, stored):
    try:
        s,d=stored.split(":",1); got=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(s),200_000).hex(); return hmac.compare_digest(got,d)
    except Exception: return False
def sign(value): return value+"."+hmac.new(SECRET.encode(),value.encode(),hashlib.sha256).hexdigest()
def unsign(token):
    try:
        value,sig=token.rsplit(".",1); good=hmac.new(SECRET.encode(),value.encode(),hashlib.sha256).hexdigest(); return value if hmac.compare_digest(sig,good) else None
    except Exception: return None

def user_from_request(request):
    from .supabase_auth import AUTH_ENABLED, verified_identity
    if AUTH_ENABLED:
        identity=verified_identity(request)
        if not identity: return None
        sid=identity.get('id'); email=(identity.get('email') or '').lower()
        with db() as con:
            u=con.execute('SELECT * FROM users WHERE supabase_user_id=?',(sid,)).fetchone()
            if not u and email:
                u=con.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                if u: con.execute('UPDATE users SET supabase_user_id=? WHERE id=?',(sid,u['id']))
            return u
    raw=request.cookies.get("ll_session"); uid=unsign(raw) if raw else None
    if not uid or not uid.isdigit(): return None
    with db() as con: return con.execute("SELECT * FROM users WHERE id=?",(int(uid),)).fetchone()
def require_user(request):
    u=user_from_request(request)
    if not u: raise HTTPException(401,"Login required")
    return u

SCHEMA="""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('customer','driver','business','admin')),phone TEXT DEFAULT '',active INTEGER DEFAULT 1,verified INTEGER DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS driver_profiles(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,online INTEGER DEFAULT 0,vehicle TEXT DEFAULT '',plate TEXT DEFAULT '',rating REAL DEFAULT 5.0,completed INTEGER DEFAULT 0,latitude REAL,longitude REAL,payout_balance_cents INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS business_profiles(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,business_name TEXT NOT NULL,address TEXT DEFAULT '',rating REAL DEFAULT 5.0,plan TEXT DEFAULT 'free');
CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY AUTOINCREMENT,customer_id INTEGER NOT NULL REFERENCES users(id),business_id INTEGER REFERENCES users(id),driver_id INTEGER REFERENCES users(id),pickup TEXT NOT NULL,dropoff TEXT NOT NULL,item_description TEXT NOT NULL,distance_miles REAL NOT NULL DEFAULT 1,quoted_cents INTEGER NOT NULL,platform_fee_cents INTEGER NOT NULL,driver_pay_cents INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'posted',notes TEXT DEFAULT '',proof TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,accepted_at TEXT,picked_up_at TEXT,delivered_at TEXT,cancelled_at TEXT);
CREATE TABLE IF NOT EXISTS delivery_events(id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id INTEGER NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,actor_id INTEGER REFERENCES users(id),event TEXT NOT NULL,metadata TEXT DEFAULT '{}',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL,read_at TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ratings(id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id INTEGER NOT NULL REFERENCES deliveries(id),from_user_id INTEGER NOT NULL REFERENCES users(id),to_user_id INTEGER NOT NULL REFERENCES users(id),stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),comment TEXT DEFAULT '',created_at TEXT NOT NULL,UNIQUE(delivery_id,from_user_id,to_user_id));
CREATE TABLE IF NOT EXISTS disputes(id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id INTEGER REFERENCES deliveries(id),opened_by INTEGER NOT NULL REFERENCES users(id),category TEXT NOT NULL,details TEXT NOT NULL,status TEXT DEFAULT 'open',resolution TEXT DEFAULT '',created_at TEXT NOT NULL,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS promos(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE NOT NULL,percent_off INTEGER DEFAULT 0,cents_off INTEGER DEFAULT 0,active INTEGER DEFAULT 1,max_uses INTEGER DEFAULT 0,uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id),delivery_id INTEGER REFERENCES deliveries(id),kind TEXT NOT NULL,amount_cents INTEGER NOT NULL,note TEXT DEFAULT '',created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id,read_at);
"""
def init_db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)
        try: con.execute('ALTER TABLE users ADD COLUMN supabase_user_id TEXT')
        except Exception: pass
        try: con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_supabase_id ON users(supabase_user_id)')
        except Exception: pass
        if ADMIN_EMAIL and ADMIN_PASSWORD and not con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone(): con.execute("INSERT INTO users(email,password_hash,name,role,verified,created_at) VALUES(?,?,?,?,1,?)",(ADMIN_EMAIL,hash_password(ADMIN_PASSWORD),"LocalLoop Admin","admin",now()))
@app.on_event("startup")
def startup(): init_db()
def money(c): return f"${c/100:,.2f}"
def quote(distance):
    subtotal=round((BASE_FARE+round(max(distance,0)*PER_MILE))*SURGE); return {"customer_total_cents":subtotal+PLATFORM_FEE,"platform_fee_cents":PLATFORM_FEE,"driver_pay_cents":subtotal}
def notify(con,uid,title,body): con.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(uid,title,body,now()))
def event(con,did,actor,name,metadata=None): con.execute("INSERT INTO delivery_events(delivery_id,actor_id,event,metadata,created_at) VALUES(?,?,?,?,?)",(did,actor,name,json.dumps(metadata or {}),now()))
def page(request,name,**ctx): return templates.TemplateResponse(request,name,{"user":user_from_request(request),"money":money,**ctx})

@app.get("/",response_class=HTMLResponse)
def home(request:Request):
    with db() as con: stats={"drivers":con.execute("SELECT COUNT(*) c FROM driver_profiles WHERE online=1").fetchone()["c"],"active":con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()["c"],"complete":con.execute("SELECT COUNT(*) c FROM deliveries WHERE status='delivered'").fetchone()["c"]}
    return page(request,"index.html",stats=stats)
@app.get("/register",response_class=HTMLResponse)
def register_page(request:Request): return page(request,"register.html")
@app.post("/register")
def register(email:str=Form(...),password:str=Form(...),name:str=Form(...),role:str=Form(...),business_name:str=Form("")):
    if role not in {"customer","driver","business"}: raise HTTPException(400,"Bad role")
    from .supabase_auth import AUTH_ENABLED, sign_up, set_auth_cookies
    auth=sign_up(email.lower().strip(),password,name.strip(),role) if AUTH_ENABLED else None
    sid=(auth.get('user') or {}).get('id') if auth else None
    try:
        with db() as con:
            cur=con.execute("INSERT INTO users(email,password_hash,name,role,created_at,supabase_user_id) VALUES(?,?,?,?,?,?)",(email.lower().strip(),hash_password(password) if not AUTH_ENABLED else 'supabase-managed',name.strip(),role,now(),sid)); uid=cur.lastrowid
            if role=="driver": con.execute("INSERT INTO driver_profiles(user_id) VALUES(?)",(uid,))
            if role=="business": con.execute("INSERT INTO business_profiles(user_id,business_name) VALUES(?,?)",(uid,business_name.strip() or name.strip()))
    except sqlite3.IntegrityError: raise HTTPException(400,"Email already exists")
    if AUTH_ENABLED and not auth.get('access_token'): return RedirectResponse('/login?check_email=1',303)
    r=RedirectResponse("/dashboard",303)
    if AUTH_ENABLED: set_auth_cookies(r,auth,COOKIE_SECURE)
    else: r.set_cookie("ll_session",sign(str(uid)),httponly=True,samesite="lax",secure=COOKIE_SECURE)
    return r
@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request): return page(request,"login.html")
@app.post("/login")
def login(email:str=Form(...),password:str=Form(...)):
    from .supabase_auth import AUTH_ENABLED, sign_in, set_auth_cookies
    if AUTH_ENABLED:
        auth=sign_in(email.lower().strip(),password); sid=(auth.get('user') or {}).get('id')
        with db() as con:
            u=con.execute('SELECT * FROM users WHERE supabase_user_id=? OR email=?',(sid,email.lower().strip())).fetchone()
            if u and not u['supabase_user_id']: con.execute('UPDATE users SET supabase_user_id=? WHERE id=?',(sid,u['id']))
        if not u or not u['active']: raise HTTPException(403,'LocalLoop account unavailable')
        r=RedirectResponse('/dashboard',303); set_auth_cookies(r,auth,COOKIE_SECURE); return r
    with db() as con: u=con.execute("SELECT * FROM users WHERE email=?",(email.lower().strip(),)).fetchone()
    if not u or not verify_password(password,u["password_hash"]): raise HTTPException(400,"Invalid credentials")
    r=RedirectResponse("/dashboard",303); r.set_cookie("ll_session",sign(str(u["id"])),httponly=True,samesite="lax",secure=COOKIE_SECURE); return r
@app.post("/logout")
def logout():
    from .supabase_auth import clear_auth_cookies
    r=RedirectResponse("/",303); clear_auth_cookies(r); return r
@app.get("/dashboard",response_class=HTMLResponse)
def dashboard(request:Request):
    u=require_user(request)
    if u["role"]=="driver": return driver_dashboard(request)
    if u["role"]=="admin": return admin_dashboard(request)
    with db() as con: rows=con.execute("SELECT d.*,u.name driver FROM deliveries d LEFT JOIN users u ON u.id=d.driver_id WHERE d.customer_id=? ORDER BY d.id DESC",(u["id"],)).fetchall()
    return page(request,"customer.html",deliveries=rows)
@app.post("/deliveries")
def create_delivery(request:Request,pickup:str=Form(...),dropoff:str=Form(...),item_description:str=Form(...),distance_miles:float=Form(1),notes:str=Form("")):
    u=require_user(request)
    if u["role"] not in {"customer","business","admin"}: raise HTTPException(403)
    q=quote(distance_miles); t=now()
    with db() as con:
        cur=con.execute("INSERT INTO deliveries(customer_id,business_id,pickup,dropoff,item_description,distance_miles,quoted_cents,platform_fee_cents,driver_pay_cents,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(u["id"],u["id"] if u["role"]=="business" else None,pickup,dropoff,item_description,distance_miles,q["customer_total_cents"],q["platform_fee_cents"],q["driver_pay_cents"],notes,t,t)); event(con,cur.lastrowid,u["id"],"posted",q)
    return RedirectResponse("/dashboard",303)
@app.post("/deliveries/{did}/cancel")
def cancel_delivery(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute("SELECT * FROM deliveries WHERE id=?",(did,)).fetchone()
        if not d or (d["customer_id"]!=u["id"] and u["role"]!="admin"): raise HTTPException(404)
        if d["status"] not in {"posted","accepted"}: raise HTTPException(400,"Cannot cancel now")
        con.execute("UPDATE deliveries SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=?",(now(),now(),did)); event(con,did,u["id"],"cancelled")
    return RedirectResponse("/dashboard",303)

def driver_dashboard(request):
    u=require_user(request)
    if u["role"] not in {"driver","admin"}: raise HTTPException(403)
    with db() as con:
        profile=con.execute("SELECT * FROM driver_profiles WHERE user_id=?",(u["id"],)).fetchone()
        if not profile and u["role"]=="admin": return admin_dashboard(request)
        available=con.execute("SELECT * FROM deliveries WHERE status='posted' ORDER BY id DESC LIMIT 50").fetchall(); mine=con.execute("SELECT * FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC",(u["id"],)).fetchall()
    return page(request,"driver.html",profile=profile,available=available,mine=mine)
@app.post("/driver/online")
def driver_online(request:Request,online:int=Form(...)):
    u=require_user(request)
    if u["role"]!="driver": raise HTTPException(403)
    with db() as con: con.execute("UPDATE driver_profiles SET online=? WHERE user_id=?",(1 if online else 0,u["id"]))
    return RedirectResponse("/dashboard",303)
@app.post("/deliveries/{did}/accept")
def accept(did:int,request:Request):
    u=require_user(request)
    if u["role"]!="driver": raise HTTPException(403)
    with db() as con:
        cur=con.execute("UPDATE deliveries SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted'",(u["id"],now(),now(),did))
        if cur.rowcount!=1: raise HTTPException(409,"Already claimed")
        d=con.execute("SELECT * FROM deliveries WHERE id=?",(did,)).fetchone(); event(con,did,u["id"],"accepted"); notify(con,d["customer_id"],"Driver assigned",f"Delivery #{did} was accepted.")
    return RedirectResponse("/dashboard",303)
@app.post("/deliveries/{did}/status")
def status(did:int,request:Request,status:str=Form(...),proof:str=Form("")):
    u=require_user(request)
    if u["role"]!="driver": raise HTTPException(403)
    with db() as con:
        d=con.execute("SELECT * FROM deliveries WHERE id=? AND driver_id=?",(did,u["id"])).fetchone()
        if not d: raise HTTPException(404)
        if status=="picked_up" and d["status"]=="accepted": con.execute("UPDATE deliveries SET status='picked_up',picked_up_at=?,updated_at=? WHERE id=?",(now(),now(),did))
        elif status=="delivered" and d["status"]=="picked_up":
            con.execute("UPDATE deliveries SET status='delivered',proof=?,delivered_at=?,updated_at=? WHERE id=?",(proof,now(),now(),did)); con.execute("UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+? WHERE user_id=?",(d["driver_pay_cents"],u["id"])); con.execute("INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)",(u["id"],did,"driver_earning",d["driver_pay_cents"],"Delivery earning",now())); con.execute("INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,?,?,?,?,?)",(did,"platform_fee",d["platform_fee_cents"],"Platform revenue",now()))
        else: raise HTTPException(400,"Invalid transition")
        event(con,did,u["id"],status); notify(con,d["customer_id"],"Delivery update",f"Delivery #{did}: {status.replace('_',' ')}")
    return RedirectResponse("/dashboard",303)

def admin_dashboard(request):
    u=require_user(request)
    if u["role"]!="admin": raise HTTPException(403)
    with db() as con:
        stats={"users":con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],"online":con.execute("SELECT COUNT(*) c FROM driver_profiles WHERE online=1").fetchone()["c"],"active":con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()["c"],"today":con.execute("SELECT COUNT(*) c FROM deliveries WHERE created_at>=date('now')").fetchone()["c"],"revenue":con.execute("SELECT COALESCE(SUM(amount_cents),0) c FROM ledger WHERE kind='platform_fee'").fetchone()["c"],"disputes":con.execute("SELECT COUNT(*) c FROM disputes WHERE status='open'").fetchone()["c"]}; rows=con.execute("SELECT d.*,c.name customer,dr.name driver FROM deliveries d JOIN users c ON c.id=d.customer_id LEFT JOIN users dr ON dr.id=d.driver_id ORDER BY d.id DESC LIMIT 100").fetchall()
    return page(request,"admin.html",stats=stats,deliveries=rows)
@app.get("/api/me")
def api_me(request:Request):
    u=require_user(request); return {k:u[k] for k in ("id","email","name","role","verified","created_at")}
@app.get("/api/deliveries")
def api_deliveries(request:Request):
    u=require_user(request)
    with db() as con:
        if u["role"]=="admin": rows=con.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT 100").fetchall()
        elif u["role"]=="driver": rows=con.execute("SELECT * FROM deliveries WHERE status='posted' OR driver_id=? ORDER BY id DESC",(u["id"],)).fetchall()
        else: rows=con.execute("SELECT * FROM deliveries WHERE customer_id=? ORDER BY id DESC",(u["id"],)).fetchall()
    return [dict(r) for r in rows]
@app.get("/api/notifications")
def api_notifications(request:Request):
    u=require_user(request)
    with db() as con: return [dict(r) for r in con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100",(u["id"],)).fetchall()]
@app.get("/api/quote")
def api_quote(distance_miles:float=1): return quote(distance_miles)
@app.get("/health")
def health(): return JSONResponse({"ok":True,"service":"localloop"})
