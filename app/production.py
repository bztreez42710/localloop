from __future__ import annotations
import os, re, base64
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from .main import app, db, now, page, require_user, notify, event, quote

TWILIO_ACCOUNT_SID=os.environ.get('TWILIO_ACCOUNT_SID','').strip()
TWILIO_AUTH_TOKEN=os.environ.get('TWILIO_AUTH_TOKEN','').strip()
TWILIO_FROM=os.environ.get('TWILIO_FROM_NUMBER','').strip()
SPOKANE_ZIP_PREFIX='992'

def _add_column(con, table, definition):
    try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
    except Exception: pass

def _is_spokane(address:str)->bool:
    a=(address or '').lower(); z=re.search(r'\b(\d{5})\b',a)
    if z and not z.group(1).startswith(SPOKANE_ZIP_PREFIX): return False
    return ('spokane' in a and ('wa' in a or 'washington' in a)) or bool(z and z.group(1).startswith(SPOKANE_ZIP_PREFIX))

def _distance_estimate(pickup:str,dropoff:str,user_distance:float)->float:
    return max(0.1,min(float(user_distance or 1),60.0))

def _safe_image(data:str,max_bytes:int=1_750_000)->str:
    if not data: return ''
    if not data.startswith('data:image/'): raise HTTPException(400,'Photo proof must be an image.')
    try: raw=base64.b64decode(data.split(',',1)[1],validate=False)
    except Exception: raise HTTPException(400,'Could not read photo proof.')
    if len(raw)>max_bytes: raise HTTPException(400,'Photo is too large. Please use a smaller image.')
    return data

@app.on_event('startup')
def production_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS driver_compliance(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            identity_status TEXT DEFAULT 'pending',background_status TEXT DEFAULT 'pending',insurance_status TEXT DEFAULT 'pending',
            background_consent INTEGER DEFAULT 0,insurance_company TEXT DEFAULT '',insurance_policy_last4 TEXT DEFAULT '',insurance_expires TEXT DEFAULT '',
            payout_email TEXT DEFAULT '',updated_at TEXT DEFAULT '')''')
        con.execute('''CREATE TABLE IF NOT EXISTS payment_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id),shopping_order_id INTEGER,delivery_id INTEGER,
            provider TEXT DEFAULT 'finix',provider_ref TEXT DEFAULT '',amount_cents INTEGER DEFAULT 0,status TEXT DEFAULT 'created',kind TEXT DEFAULT '',created_at TEXT NOT NULL)''')
        _add_column(con,'driver_compliance',"payout_email TEXT DEFAULT ''")
        _add_column(con,'deliveries','proof_photo TEXT DEFAULT \'\'')
        _add_column(con,'deliveries','pickup_lat REAL'); _add_column(con,'deliveries','pickup_lng REAL')
        _add_column(con,'deliveries','dropoff_lat REAL'); _add_column(con,'deliveries','dropoff_lng REAL')
        _add_column(con,'shopping_orders','estimated_goods_cents INTEGER DEFAULT 0')
        _add_column(con,'shopping_orders','actual_goods_cents INTEGER DEFAULT 0')
        _add_column(con,'shopping_orders',"payment_provider_ref TEXT DEFAULT ''")
        _add_column(con,'shopping_orders','payment_status TEXT DEFAULT \'unfunded\'')
        _add_column(con,'shopping_orders','receipt_photo TEXT DEFAULT \'\'')

for r in list(app.router.routes):
    p=getattr(r,'path',None); methods=getattr(r,'methods',set()) or set()
    if (p=='/deliveries' and 'POST' in methods) or (p=='/driver/online' and 'POST' in methods) or (p=='/deliveries/{did}/status' and 'POST' in methods): app.router.routes.remove(r)

@app.post('/deliveries')
def create_delivery_production(request:Request,pickup:str=Form(...),dropoff:str=Form(...),item_description:str=Form(...),distance_miles:float=Form(1),notes:str=Form('')):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    if not _is_spokane(pickup) or not _is_spokane(dropoff): raise HTTPException(400,'LocalLoop currently serves Spokane, Washington only. Please use Spokane addresses (992xx).')
    miles=_distance_estimate(pickup,dropoff,distance_miles); q=quote(miles); t=now()
    with db() as con:
        cur=con.execute('INSERT INTO deliveries(customer_id,business_id,pickup,dropoff,item_description,distance_miles,quoted_cents,platform_fee_cents,driver_pay_cents,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(u['id'],u['id'] if u['role']=='business' else None,pickup.strip(),dropoff.strip(),item_description.strip(),miles,q['customer_total_cents'],q['platform_fee_cents'],q['driver_pay_cents'],notes.strip(),t,t)); event(con,cur.lastrowid,u['id'],'posted',q)
    return RedirectResponse('/dashboard',303)

@app.get('/driver/setup',response_class=HTMLResponse)
def driver_setup_page(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(u['id'],now())); c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'driver_setup.html',compliance=c)

@app.post('/driver/setup')
def driver_setup_save(request:Request,background_consent:int=Form(0),insurance_company:str=Form(''),insurance_policy_last4:str=Form(''),insurance_expires:str=Form(''),payout_email:str=Form('')):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    last4=re.sub(r'\D','',insurance_policy_last4)[-4:]
    if payout_email and '@' not in payout_email: raise HTTPException(400,'Enter a valid payout contact email.')
    if insurance_policy_last4 and len(last4)!=4: raise HTTPException(400,'Enter the last 4 digits of your insurance policy number.')
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(u['id'],now()))
        con.execute('UPDATE driver_compliance SET background_consent=?,insurance_company=?,insurance_policy_last4=?,insurance_expires=?,payout_email=?,updated_at=? WHERE user_id=?',(1 if background_consent else 0,insurance_company.strip(),last4,insurance_expires.strip(),payout_email.strip().lower(),now(),u['id']))
    return RedirectResponse('/driver/verify/status?setup_saved=1',303)

@app.post('/driver/online')
def driver_online_production(request:Request,online:int=Form(...)):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(u['id'],now())); c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if online and (not c or c['identity_status']!='approved' or c['background_status']!='approved' or c['insurance_status']!='approved'): raise HTTPException(403,'Driver verification is not complete. Finish Driver Setup and wait for approval before going online.')
        con.execute('UPDATE driver_profiles SET online=? WHERE user_id=?',(1 if online else 0,u['id']))
    return RedirectResponse('/dashboard',303)

@app.get('/admin/drivers',response_class=HTMLResponse)
def admin_drivers(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        rows=con.execute("SELECT u.id,u.name,u.email,d.vehicle,d.plate,c.identity_status,c.background_status,c.insurance_status,c.background_consent,c.insurance_company,c.insurance_policy_last4,c.insurance_expires,c.payout_email FROM users u LEFT JOIN driver_profiles d ON d.user_id=u.id LEFT JOIN driver_compliance c ON c.user_id=u.id WHERE u.role='driver' ORDER BY u.id DESC").fetchall()
    return page(request,'admin_drivers.html',drivers=rows)

@app.post('/admin/drivers/{uid}/review')
def admin_driver_review(uid:int,request:Request,identity_status:str=Form(...),background_status:str=Form(...),insurance_status:str=Form(...)):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    allowed={'pending','approved','rejected'}
    if any(x not in allowed for x in (identity_status,background_status,insurance_status)): raise HTTPException(400)
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(uid,now())); con.execute('UPDATE driver_compliance SET identity_status=?,background_status=?,insurance_status=?,updated_at=? WHERE user_id=?',(identity_status,background_status,insurance_status,now(),uid))
    return RedirectResponse('/admin/drivers',303)

@app.post('/driver/location')
def driver_location(request:Request,latitude:float=Form(...),longitude:float=Form(...)):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    if not (47.45 <= latitude <= 47.85 and -117.75 <= longitude <= -117.05): raise HTTPException(400,'Location is outside the Spokane service area.')
    with db() as con: con.execute('UPDATE driver_profiles SET latitude=?,longitude=? WHERE user_id=?',(latitude,longitude,u['id']))
    return JSONResponse({'ok':True})

@app.get('/api/track/{did}')
def api_track(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
        if not d: raise HTTPException(404)
        if u['role']!='admin' and d['customer_id']!=u['id'] and d['driver_id']!=u['id']: raise HTTPException(403)
        p=con.execute('SELECT latitude,longitude FROM driver_profiles WHERE user_id=?',(d['driver_id'],)).fetchone() if d['driver_id'] else None
    return JSONResponse({'delivery_id':did,'status':d['status'],'driver':dict(p) if p else None})

@app.get('/api/notifications')
def api_notifications(request:Request):
    u=require_user(request)
    with db() as con: rows=con.execute('SELECT id,title,body,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 10',(u['id'],)).fetchall()
    return JSONResponse([dict(r) for r in rows])

@app.post('/deliveries/{did}/status')
def delivery_status_production(did:int,request:Request,status:str=Form(...),proof:str=Form(''),proof_photo:str=Form('')):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    photo=_safe_image(proof_photo) if proof_photo else ''
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=? AND driver_id=?',(did,u['id'])).fetchone()
        if not d: raise HTTPException(404)
        if status=='picked_up' and d['status']=='accepted': con.execute("UPDATE deliveries SET status='picked_up',picked_up_at=?,updated_at=? WHERE id=?",(now(),now(),did))
        elif status=='delivered' and d['status']=='picked_up':
            con.execute("UPDATE deliveries SET status='delivered',proof=?,proof_photo=?,delivered_at=?,updated_at=? WHERE id=?",(proof.strip(),photo,now(),now(),did)); con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(d['driver_pay_cents'],u['id']))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)',(u['id'],did,'driver_earning',d['driver_pay_cents'],'Delivery earning',now())); con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,?,?,?,?,?)',(did,'platform_fee',d['platform_fee_cents'],'Platform revenue',now()))
        else: raise HTTPException(400,'Invalid transition')
        event(con,did,u['id'],status); notify(con,d['customer_id'],'Delivery update',f"Delivery #{did}: {status.replace('_',' ')}")
    return RedirectResponse('/dashboard',303)
