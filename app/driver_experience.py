from __future__ import annotations
import base64, csv, io, itertools, json, math, time, hmac, hashlib
from datetime import datetime, timezone, timedelta
from fastapi import Request, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, Response
import httpx
from .main import app, now, require_user, notify, sign, unsign
from .database import db
from .mobile_api import _token_user, _shopping_job
from .routing import geocode_address, OSRM, UA
from .payments import stripe, configured, STRIPE_WEBHOOK_SECRET, _session_paid, _mark_shopping_paid, _mark_marketplace_paid


def _table(con, sql):
    con.execute(sql)

def _col(con, table, definition):
    try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
    except Exception: pass

@app.on_event('startup')
def driver_experience_startup():
    with db() as con:
        _col(con,'driver_location_history','segment_miles REAL DEFAULT 0')
        _col(con,'driver_location_history','route_deviation INTEGER DEFAULT 0')
        _col(con,'driver_profiles',"location_updated_at TEXT DEFAULT ''")
        _table(con,'''CREATE TABLE IF NOT EXISTS driver_expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,driver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_cents INTEGER NOT NULL,category TEXT DEFAULT 'other',note TEXT DEFAULT '',created_at TEXT NOT NULL)''')
        _table(con,'''CREATE TABLE IF NOT EXISTS driver_incidents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,driver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delivery_id INTEGER,shopping_order_id INTEGER,kind TEXT NOT NULL,details TEXT DEFAULT '',latitude REAL,longitude REAL,
            created_at TEXT NOT NULL,resolved_at TEXT DEFAULT '')''')
        _table(con,'''CREATE TABLE IF NOT EXISTS job_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id INTEGER,shopping_order_id INTEGER,sender_id INTEGER NOT NULL REFERENCES users(id),
            kind TEXT DEFAULT 'message',body TEXT NOT NULL,created_at TEXT NOT NULL)''')
        _table(con,'''CREATE TABLE IF NOT EXISTS shopping_substitutions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,shopping_order_id INTEGER NOT NULL,driver_id INTEGER NOT NULL REFERENCES users(id),
            original_item TEXT NOT NULL,proposed_item TEXT NOT NULL,photo_data TEXT DEFAULT '',status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,decided_at TEXT DEFAULT '')''')
        _table(con,'''CREATE TABLE IF NOT EXISTS delivery_proof_packages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,driver_id INTEGER NOT NULL REFERENCES users(id),delivery_id INTEGER,shopping_order_id INTEGER,
            note TEXT DEFAULT '',delivery_photo TEXT DEFAULT '',receipt_photo TEXT DEFAULT '',signature TEXT DEFAULT '',customer_pin TEXT DEFAULT '',
            latitude REAL,longitude REAL,accuracy_m REAL DEFAULT 0,created_at TEXT NOT NULL)''')
        _table(con,'''CREATE TABLE IF NOT EXISTS driver_tips(
            id INTEGER PRIMARY KEY AUTOINCREMENT,customer_id INTEGER NOT NULL REFERENCES users(id),driver_id INTEGER NOT NULL REFERENCES users(id),
            delivery_id INTEGER,shopping_order_id INTEGER,amount_cents INTEGER NOT NULL,stripe_session_id TEXT DEFAULT '',status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,paid_at TEXT DEFAULT '')''')
        _table(con,'''CREATE TABLE IF NOT EXISTS shopping_route_preferences(
            shopping_order_id INTEGER PRIMARY KEY,driver_id INTEGER NOT NULL REFERENCES users(id),mode TEXT DEFAULT 'optimized',stop_order TEXT DEFAULT '[]',updated_at TEXT NOT NULL)''')
        for sql in [
            'CREATE INDEX IF NOT EXISTS idx_expenses_driver ON driver_expenses(driver_id,id)',
            'CREATE INDEX IF NOT EXISTS idx_incidents_driver ON driver_incidents(driver_id,id)',
            'CREATE INDEX IF NOT EXISTS idx_messages_delivery ON job_messages(delivery_id,id)',
            'CREATE INDEX IF NOT EXISTS idx_messages_shop ON job_messages(shopping_order_id,id)',
            'CREATE INDEX IF NOT EXISTS idx_sub_shop ON shopping_substitutions(shopping_order_id,id)']:
            try: con.execute(sql)
            except Exception: pass


def _haversine(lat1,lon1,lat2,lon2):
    r=3958.7613
    a1,a2=math.radians(lat1),math.radians(lat2); da=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(da/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dl/2)**2
    return r*2*math.atan2(math.sqrt(a),math.sqrt(max(1-a,0)))


def _point_to_segment_miles(px,py,ax,ay,bx,by):
    # Equirectangular local projection is adequate for Spokane-scale deviation checks.
    lat0=math.radians((py+ay+by)/3)
    def xy(lon,lat): return (lon*69.172*math.cos(lat0),lat*69.0)
    p=xy(px,py); a=xy(ax,ay); b=xy(bx,by)
    dx,dy=b[0]-a[0],b[1]-a[1]
    if dx*dx+dy*dy==0: return math.hypot(p[0]-a[0],p[1]-a[1])
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    q=(a[0]+t*dx,a[1]+t*dy)
    return math.hypot(p[0]-q[0],p[1]-q[1])


def _route_from_coords(lat,lon,dlat,dlon):
    try:
        r=httpx.get(f'{OSRM}/route/v1/driving/{lon},{lat};{dlon},{dlat}',params={'overview':'false','steps':'false'},headers={'User-Agent':UA},timeout=10)
        r.raise_for_status(); data=r.json(); rt=(data.get('routes') or [None])[0]
        if rt: return round(rt['distance']/1609.344,2), max(1,round(rt['duration']/60))
    except Exception: pass
    miles=_haversine(lat,lon,dlat,dlon)*1.18
    return round(miles,2), max(1,round(miles/25*60))


def _gps_age(updated_at):
    if not updated_at: return None
    try:
        dt=datetime.fromisoformat(updated_at.replace('Z','+00:00')); return max(0,int((datetime.now(timezone.utc)-dt).total_seconds()))
    except Exception:return None


def _safe_photo(data, limit=900000):
    data=(data or '').strip()
    if not data:return ''
    if not data.startswith('data:image/'): raise HTTPException(400,'Photo must be an image.')
    try: raw=base64.b64decode(data.split(',',1)[1],validate=False)
    except Exception: raise HTTPException(400,'Could not read photo.')
    if len(raw)>limit: raise HTTPException(400,'Photo is too large.')
    return data


def _shop_stops(con, oid):
    return [dict(r) for r in con.execute('SELECT stop_number,store_name,store_address,shopping_list FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(oid,)).fetchall()]


def _optimized_stops(stops, start_lat=None, start_lon=None):
    geocoded=[]
    for s in stops:
        q=(s.get('store_address') or '').strip() or f"{s.get('store_name','')}, Spokane, WA"
        try:
            g=geocode_address(q); x=dict(s); x.update({'lat':g['lat'],'lon':g['lon'],'resolved_address':g['display_name']}); geocoded.append(x)
        except Exception:
            x=dict(s); x.update({'lat':None,'lon':None,'resolved_address':q}); geocoded.append(x)
    if len(geocoded)<=1 or any(x['lat'] is None for x in geocoded): return geocoded
    best=None
    for perm in itertools.permutations(geocoded):
        dist=0.0
        if start_lat is not None: dist+=_haversine(start_lat,start_lon,perm[0]['lat'],perm[0]['lon'])
        for a,b in zip(perm,perm[1:]): dist+=_haversine(a['lat'],a['lon'],b['lat'],b['lon'])
        if best is None or dist<best[0]: best=(dist,perm)
    return list(best[1]) if best else geocoded


def _active_job(con, uid):
    d=con.execute("SELECT * FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    if d:return ('delivery',d)
    s=con.execute("SELECT * FROM shopping_orders WHERE driver_id=? AND status IN ('accepted','shopping','delivering') ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    return ('shopping',s) if s else (None,None)


def _current_job_payload(con, uid):
    kind,row=_active_job(con,uid)
    if not row:return None
    p=con.execute('SELECT latitude,longitude,location_updated_at FROM driver_profiles WHERE user_id=?',(uid,)).fetchone()
    lat=float(p['latitude']) if p and p['latitude'] is not None else None; lon=float(p['longitude']) if p and p['longitude'] is not None else None
    gps_age=_gps_age(p['location_updated_at'] if p else '')
    if kind=='delivery':
        next_label='Pickup' if row['status']=='accepted' else 'Customer drop-off'
        address=row['pickup'] if row['status']=='accepted' else row['dropoff']
        stage='Heading to pickup' if row['status']=='accepted' else 'Heading to customer'
        note=row['notes'] or ''
        pay=int(row['driver_pay_cents'])
        try:g=geocode_address(address); dlat,dlon=g['lat'],g['lon']
        except Exception:dlat=dlon=None
        stop_count=1
    else:
        stops=_shop_stops(con,row['id'])
        pref=con.execute('SELECT mode,stop_order FROM shopping_route_preferences WHERE shopping_order_id=?',(row['id'],)).fetchone()
        ordered=_optimized_stops(stops,lat,lon) if (not pref or pref['mode']=='optimized') else stops
        if row['status']=='delivering' or not ordered:
            next_label='Customer drop-off'; address=row['dropoff']; stage='Heading to customer'
            try:g=geocode_address(address); dlat,dlon=g['lat'],g['lon']
            except Exception:dlat=dlon=None
        else:
            current=ordered[0]; next_label=current['store_name']; address=current.get('resolved_address') or current.get('store_address') or current['store_name']; stage='Heading to store' if row['status']=='accepted' else 'Shopping'
            dlat,dlon=current.get('lat'),current.get('lon')
        note=row['notes'] or ''; pay=int(row['driver_pay_cents']); stop_count=len(stops)
    eta=None; distance=None; arrived=False
    if lat is not None and dlat is not None:
        distance,eta=_route_from_coords(lat,lon,dlat,dlon); arrived=_haversine(lat,lon,dlat,dlon)<=0.12
    deviation=False
    if kind=='delivery' and lat is not None:
        try:
            a=geocode_address(row['pickup']); b=geocode_address(row['dropoff'])
            deviation=_point_to_segment_miles(lon,lat,a['lon'],a['lat'],b['lon'],b['lat'])>.75
        except Exception: pass
    miles=0.0
    rows=con.execute('SELECT segment_miles FROM driver_location_history WHERE driver_id=? AND '+('delivery_id=?' if kind=='delivery' else 'shopping_order_id=?'),(uid,row['id'])).fetchall()
    miles=round(sum(float(r['segment_miles'] or 0) for r in rows),2)
    return {'kind':kind,'id':row['id'],'status':row['status'],'stage':stage,'next_label':next_label,'next_address':address,'notes':note,'expected_earnings_cents':pay,'eta_minutes':eta,'distance_to_next_miles':distance,'arrived':arrived,'gps_age_seconds':gps_age,'gps_stale':gps_age is None or gps_age>45,'route_deviation':deviation,'trip_miles':miles,'stop_count':stop_count}


def _money(con, uid):
    p=con.execute('SELECT payout_balance_cents,completed FROM driver_profiles WHERE user_id=?',(uid,)).fetchone()
    available=int(p['payout_balance_cents'] if p else 0); completed=int(p['completed'] if p else 0)
    pending=con.execute("SELECT COALESCE(SUM(amount_cents),0) c FROM payment_records WHERE user_id=? AND kind='shopper_payout' AND status='pending'",(uid,)).fetchone()['c']
    earnings=con.execute("SELECT COALESCE(SUM(amount_cents),0) c FROM ledger WHERE user_id=? AND kind IN ('driver_earning','shopping_reimbursement','driver_tip')",(uid,)).fetchone()['c']
    expenses=con.execute('SELECT COALESCE(SUM(amount_cents),0) c FROM driver_expenses WHERE driver_id=?',(uid,)).fetchone()['c']
    payouts=[]
    try:payouts=[dict(r) for r in con.execute('SELECT amount_cents,status,created_at FROM driver_payout_requests WHERE driver_id=? ORDER BY id DESC LIMIT 20',(uid,)).fetchall()]
    except Exception:pass
    miles=con.execute('SELECT COALESCE(SUM(segment_miles),0) c FROM driver_location_history WHERE driver_id=?',(uid,)).fetchone()['c']
    return {'available_cents':available,'pending_cents':int(pending or 0),'lifetime_earnings_cents':int(earnings or 0),'expense_cents':int(expenses or 0),'estimated_profit_cents':int(earnings or 0)-int(expenses or 0),'completed':completed,'active_miles':round(float(miles or 0),2),'payout_history':payouts}


def _enhanced_offers(con):
    offers=[]
    for r in con.execute("SELECT * FROM deliveries WHERE status='posted' ORDER BY id DESC LIMIT 40").fetchall():
        x=dict(r); miles=max(.1,float(x.get('distance_miles') or 0)); x.update({'job_type':'delivery','stop_count':1,'estimated_minutes':max(5,round(miles/24*60)),'complexity':'Standard delivery','pay_per_mile':round(int(x['driver_pay_cents'])/100/miles,2)}); offers.append(x)
    for r in con.execute("SELECT * FROM shopping_orders WHERE status='posted' AND payment_status='funded' ORDER BY id DESC LIMIT 40").fetchall():
        x=_shopping_job(con,r); stops=_shop_stops(con,r['id']); complexity='Simple' if len(stops)==1 else ('Multi-store' if len(stops)<=3 else 'Complex'); est=max(20,len(stops)*18); x.update({'stop_count':len(stops),'estimated_minutes':est,'complexity':complexity,'pay_per_mile':None}); offers.append(x)
    return offers

@app.get('/api/mobile/smart-dashboard')
def smart_dashboard(request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online,location_updated_at FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        return {'driver':{'id':u['id'],'name':u['name'],'online':bool(p and p['online'])},'current_job':_current_job_payload(con,u['id']),'money':_money(con,u['id']),'offers':_enhanced_offers(con)}

# Replace location endpoint again so movement mileage and deviation are recorded.
for r in list(app.router.routes):
    if getattr(r,'path',None)=='/api/mobile/location' and 'POST' in (getattr(r,'methods',set()) or set()): app.router.routes.remove(r)

@app.post('/api/mobile/location')
def rich_location(request:Request,latitude:float=Form(...),longitude:float=Form(...),speed_mps:float=Form(0),bearing:float=Form(0),accuracy_m:float=Form(0)):
    u,_=_token_user(request)
    if not (47.45<=latitude<=47.85 and -117.75<=longitude<=-117.05): raise HTTPException(400,'Location is outside the Spokane service area')
    with db() as con:
        p=con.execute('SELECT online,latitude,longitude FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone(); kind,row=_active_job(con,u['id'])
        if not p or not p['online'] or not row: raise HTTPException(409,'Location sharing is only available while online with an active job')
        seg=0.0
        if p['latitude'] is not None and p['longitude'] is not None:
            seg=_haversine(float(p['latitude']),float(p['longitude']),latitude,longitude)
            if seg>1.5: seg=0.0
        speed=max(0,min(float(speed_mps or 0)*2.236936,120)); acc=max(0,min(float(accuracy_m or 0),1000)); br=max(0,min(float(bearing or 0),360))
        con.execute('UPDATE driver_profiles SET latitude=?,longitude=?,location_updated_at=? WHERE user_id=?',(latitude,longitude,now(),u['id']))
        con.execute('INSERT INTO driver_location_history(driver_id,delivery_id,shopping_order_id,latitude,longitude,speed_mph,bearing,accuracy_m,segment_miles,route_deviation,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u['id'],row['id'] if kind=='delivery' else None,row['id'] if kind=='shopping' else None,latitude,longitude,speed,br,acc,seg,0,now()))
    return {'ok':True,'speed_mph':round(speed,1),'accuracy_m':round(acc,1),'segment_miles':round(seg,3)}

@app.post('/api/mobile/incidents')
def mobile_incident(request:Request,kind:str=Form('sos'),details:str=Form(''),latitude:float=Form(0),longitude:float=Form(0)):
    u,_=_token_user(request)
    with db() as con:
        jk,jr=_active_job(con,u['id'])
        con.execute('INSERT INTO driver_incidents(driver_id,delivery_id,shopping_order_id,kind,details,latitude,longitude,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],jr['id'] if jk=='delivery' else None,jr['id'] if jk=='shopping' else None,kind[:40],details[:1000],latitude or None,longitude or None,now()))
    return {'ok':True}

@app.get('/api/admin/incidents')
def admin_incidents(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con: rows=[dict(r) for r in con.execute('SELECT i.*,u.name driver FROM driver_incidents i JOIN users u ON u.id=i.driver_id ORDER BY i.id DESC LIMIT 300').fetchall()]
    return JSONResponse(rows)

@app.post('/api/mobile/expenses')
def add_expense(request:Request,amount_cents:int=Form(...),category:str=Form('other'),note:str=Form('')):
    u,_=_token_user(request); amount=max(1,min(int(amount_cents),1000000))
    with db() as con: con.execute('INSERT INTO driver_expenses(driver_id,amount_cents,category,note,created_at) VALUES(?,?,?,?,?)',(u['id'],amount,category[:40],note[:500],now()))
    return {'ok':True}

@app.get('/api/mobile/money')
def money_api(request:Request):
    u,_=_token_user(request)
    with db() as con: data=_money(con,u['id'])
    expiry=int(time.time())+900; token=sign(f"{u['id']}:{expiry}")
    data['weekly_report_url']=f'https://localloop-app.onrender.com/driver/report/{token}.csv?period=week'; data['monthly_report_url']=f'https://localloop-app.onrender.com/driver/report/{token}.csv?period=month'
    return data

@app.get('/driver/report/{token}.csv')
def driver_report(token:str,period:str='week'):
    raw=unsign(token)
    if not raw or ':' not in raw: raise HTTPException(403)
    uid_s,exp_s=raw.split(':',1)
    if not uid_s.isdigit() or not exp_s.isdigit() or int(exp_s)<int(time.time()): raise HTTPException(403,'Report link expired')
    uid=int(uid_s); days=7 if period=='week' else 31
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    out=io.StringIO(); w=csv.writer(out); w.writerow(['Date','Type','Amount','Note'])
    with db() as con:
        for r in con.execute('SELECT created_at,kind,amount_cents,note FROM ledger WHERE user_id=? AND created_at>=? ORDER BY created_at',(uid,cutoff)).fetchall(): w.writerow([r['created_at'],r['kind'],f"{r['amount_cents']/100:.2f}",r['note']])
        w.writerow([]); w.writerow(['Active-job mileage']); miles=con.execute('SELECT COALESCE(SUM(segment_miles),0) c FROM driver_location_history WHERE driver_id=? AND created_at>=?',(uid,cutoff)).fetchone()['c']; w.writerow([f'{float(miles or 0):.2f} miles'])
        expenses=con.execute('SELECT COALESCE(SUM(amount_cents),0) c FROM driver_expenses WHERE driver_id=? AND created_at>=?',(uid,cutoff)).fetchone()['c']; w.writerow(['Expenses',f'${int(expenses or 0)/100:.2f}'])
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="localloop-{period}-summary.csv"'})

PRESETS={'at_store':"I'm at the store.",'unavailable':'An item is unavailable.','substitution':'I sent a substitution option.','outside':"I'm outside.",'message':''}

@app.post('/api/mobile/messages')
def driver_message(request:Request,kind:str=Form('message'),body:str=Form('')):
    u,_=_token_user(request); k=kind if kind in PRESETS else 'message'; text=(body.strip() or PRESETS.get(k,''))[:1000]
    if not text: raise HTTPException(400,'Message is empty')
    with db() as con:
        jk,jr=_active_job(con,u['id'])
        if not jr: raise HTTPException(409,'No active job')
        con.execute('INSERT INTO job_messages(delivery_id,shopping_order_id,sender_id,kind,body,created_at) VALUES(?,?,?,?,?,?)',(jr['id'] if jk=='delivery' else None,jr['id'] if jk=='shopping' else None,u['id'],k,text,now()))
        notify(con,jr['customer_id'],'Driver message',text)
    return {'ok':True}

@app.post('/api/mobile/substitutions')
def driver_substitution(request:Request,original_item:str=Form(...),proposed_item:str=Form(...),photo_data:str=Form('')):
    u,_=_token_user(request); photo=_safe_photo(photo_data)
    with db() as con:
        o=con.execute("SELECT * FROM shopping_orders WHERE driver_id=? AND status='shopping' ORDER BY id DESC LIMIT 1",(u['id'],)).fetchone()
        if not o: raise HTTPException(409,'No active shopping order')
        con.execute('INSERT INTO shopping_substitutions(shopping_order_id,driver_id,original_item,proposed_item,photo_data,status,created_at) VALUES(?,?,?,?,?,?,?)',(o['id'],u['id'],original_item[:250],proposed_item[:250],photo,'pending',now()))
        notify(con,o['customer_id'],'Substitution approval needed',f'{original_item[:80]} → {proposed_item[:80]}')
    return {'ok':True}

@app.get('/api/job/{kind}/{oid}/live')
def customer_live(kind:str,oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        table='deliveries' if kind=='delivery' else 'shopping_orders'
        row=con.execute(f'SELECT * FROM {table} WHERE id=?',(oid,)).fetchone()
        if not row: raise HTTPException(404)
        if u['role']!='admin' and row['customer_id']!=u['id'] and row['driver_id']!=u['id']: raise HTTPException(403)
        current=_current_job_payload(con,row['driver_id']) if row['driver_id'] and row['status'] not in {'delivered','cancelled'} else None
        msg_sql='delivery_id=?' if kind=='delivery' else 'shopping_order_id=?'
        messages=[dict(r) for r in con.execute(f'SELECT m.id,m.kind,m.body,m.created_at,u.name sender FROM job_messages m JOIN users u ON u.id=m.sender_id WHERE {msg_sql} ORDER BY m.id DESC LIMIT 20',(oid,)).fetchall()]
        subs=[]
        if kind=='shopping': subs=[dict(r) for r in con.execute('SELECT id,original_item,proposed_item,photo_data,status,created_at FROM shopping_substitutions WHERE shopping_order_id=? ORDER BY id DESC LIMIT 20',(oid,)).fetchall()]
    stages=['Paid','Driver assigned','Heading to store' if kind=='shopping' else 'Heading to pickup','Shopping' if kind=='shopping' else 'Picked up','Heading to you','Arriving','Delivered']
    return {'status':row['status'],'eta_minutes':current.get('eta_minutes') if current else None,'gps_age_seconds':current.get('gps_age_seconds') if current else None,'gps_stale':current.get('gps_stale') if current else True,'arrived':current.get('arrived') if current else False,'timeline':stages,'current_stage':current.get('stage') if current else ('Delivered' if row['status']=='delivered' else row['status']),'messages':messages,'substitutions':subs}

@app.post('/api/job/{kind}/{oid}/message')
def customer_message(kind:str,oid:int,request:Request,body:str=Form(...)):
    u=require_user(request); text=body.strip()[:1000]
    if not text: raise HTTPException(400)
    with db() as con:
        table='deliveries' if kind=='delivery' else 'shopping_orders'; row=con.execute(f'SELECT * FROM {table} WHERE id=?',(oid,)).fetchone()
        if not row or (u['role']!='admin' and row['customer_id']!=u['id']): raise HTTPException(404)
        con.execute('INSERT INTO job_messages(delivery_id,shopping_order_id,sender_id,kind,body,created_at) VALUES(?,?,?,?,?,?)',(oid if kind=='delivery' else None,oid if kind=='shopping' else None,u['id'],'message',text,now()))
        if row['driver_id']: notify(con,row['driver_id'],'Customer message',text)
    return {'ok':True}

@app.post('/api/shop/{oid}/substitutions/{sid}')
def decide_substitution(oid:int,sid:int,request:Request,status:str=Form(...)):
    u=require_user(request)
    if status not in {'approved','rejected'}: raise HTTPException(400)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and o['customer_id']!=u['id']): raise HTTPException(404)
        cur=con.execute("UPDATE shopping_substitutions SET status=?,decided_at=? WHERE id=? AND shopping_order_id=? AND status='pending'",(status,now(),sid,oid))
        if cur.rowcount and o['driver_id']: notify(con,o['driver_id'],'Substitution response',f'Customer {status} substitution #{sid}.')
    return {'ok':True,'status':status}

@app.post('/api/mobile/proof')
def save_proof(request:Request,note:str=Form(''),delivery_photo:str=Form(''),receipt_photo:str=Form(''),signature:str=Form(''),customer_pin:str=Form('')):
    u,_=_token_user(request); dp=_safe_photo(delivery_photo); rp=_safe_photo(receipt_photo)
    with db() as con:
        jk,jr=_active_job(con,u['id'])
        if not jr: raise HTTPException(409,'No active job')
        p=con.execute('SELECT latitude,longitude FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        con.execute('INSERT INTO delivery_proof_packages(driver_id,delivery_id,shopping_order_id,note,delivery_photo,receipt_photo,signature,customer_pin,latitude,longitude,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u['id'],jr['id'] if jk=='delivery' else None,jr['id'] if jk=='shopping' else None,note[:1000],dp,rp,signature[:300],customer_pin[:40],p['latitude'] if p else None,p['longitude'] if p else None,now()))
    return {'ok':True}

@app.post('/api/mobile/shopping/{oid}/route-plan')
def route_plan_mode(oid:int,request:Request,mode:str=Form('optimized')):
    u,_=_token_user(request)
    if mode not in {'optimized','original'}: raise HTTPException(400)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND driver_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        con.execute('INSERT OR REPLACE INTO shopping_route_preferences(shopping_order_id,driver_id,mode,stop_order,updated_at) VALUES(?,?,?,?,?)',(oid,u['id'],mode,'[]',now()))
    return {'ok':True,'mode':mode}

@app.post('/tip/{kind}/{oid}/start')
def tip_start(kind:str,oid:int,request:Request,amount_dollars:float=Form(...)):
    u=require_user(request)
    if not configured(): raise HTTPException(503,'Stripe is not connected')
    amount=int(round(amount_dollars*100));
    if amount<100 or amount>10000: raise HTTPException(400,'Tip must be between $1 and $100')
    with db() as con:
        table='deliveries' if kind=='delivery' else 'shopping_orders'; o=con.execute(f'SELECT * FROM {table} WHERE id=?',(oid,)).fetchone()
        if not o or o['customer_id']!=u['id'] or o['status']!='delivered' or not o['driver_id']: raise HTTPException(404)
        base=str(request.base_url).rstrip('/'); session=stripe('POST','/checkout/sessions',data={'mode':'payment','success_url':f'{base}/tip/{kind}/{oid}/success?session_id={{CHECKOUT_SESSION_ID}}','cancel_url':f'{base}/'+('track/'+str(oid) if kind=='delivery' else 'track/shop/'+str(oid)),'line_items[0][price_data][currency]':'usd','line_items[0][price_data][unit_amount]':str(amount),'line_items[0][price_data][product_data][name]':f'LocalLoop driver tip #{oid}','line_items[0][quantity]':'1','metadata[kind]':'driver_tip','metadata[target_kind]':kind,'metadata[order_id]':str(oid),'metadata[driver_id]':str(o['driver_id'])})
        con.execute('INSERT INTO driver_tips(customer_id,driver_id,delivery_id,shopping_order_id,amount_cents,stripe_session_id,status,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],o['driver_id'],oid if kind=='delivery' else None,oid if kind=='shopping' else None,amount,session.get('id',''),'pending',now()))
    return RedirectResponse(session['url'],303)


def _credit_tip(con,session):
    sid=session.get('id',''); t=con.execute('SELECT * FROM driver_tips WHERE stripe_session_id=?',(sid,)).fetchone()
    if not t or t['status']=='paid' or not _session_paid(session): return
    con.execute("UPDATE driver_tips SET status='paid',paid_at=? WHERE id=? AND status='pending'",(now(),t['id']))
    con.execute('UPDATE driver_profiles SET payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(t['amount_cents'],t['driver_id']))
    con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)',(t['driver_id'],t['delivery_id'],'driver_tip',t['amount_cents'],'Customer tip',now()))

@app.get('/tip/{kind}/{oid}/success')
def tip_success(kind:str,oid:int,request:Request,session_id:str):
    u=require_user(request); s=stripe('GET',f'/checkout/sessions/{session_id}')
    with db() as con:_credit_tip(con,s)
    return RedirectResponse('/'+('track/'+str(oid) if kind=='delivery' else 'track/shop/'+str(oid)),303)

# Extend Stripe webhook so tips remain reliable even when a customer closes the browser.
for r in list(app.router.routes):
    if getattr(r,'path',None)=='/api/payments/stripe/webhook' and 'POST' in (getattr(r,'methods',set()) or set()): app.router.routes.remove(r)

@app.post('/api/payments/stripe/webhook')
async def stripe_webhook_extended(request:Request):
    if not STRIPE_WEBHOOK_SECRET: raise HTTPException(503,'Stripe webhook is not configured.')
    body=await request.body(); sig=request.headers.get('stripe-signature',''); parts={}
    for item in sig.split(','):
        if '=' in item:
            k,v=item.split('=',1); parts.setdefault(k,[]).append(v)
    try: ts=int(parts.get('t',['0'])[0])
    except Exception: raise HTTPException(400,'Invalid Stripe signature.')
    if abs(time.time()-ts)>300: raise HTTPException(400,'Expired Stripe signature.')
    expected=hmac.new(STRIPE_WEBHOOK_SECRET.encode(),f'{ts}.'.encode()+body,hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected,v) for v in parts.get('v1',[])): raise HTTPException(400,'Invalid Stripe signature.')
    try:event=json.loads(body.decode())
    except Exception:raise HTTPException(400,'Invalid Stripe webhook payload.')
    if event.get('type') in {'checkout.session.completed','checkout.session.async_payment_succeeded'}:
        s=(event.get('data') or {}).get('object') or {}; meta=s.get('metadata') or {}
        if _session_paid(s):
            with db() as con:
                if meta.get('kind')=='shopping': _mark_shopping_paid(con,int(meta.get('order_id') or 0),s.get('id',''))
                elif meta.get('kind')=='marketplace': _mark_marketplace_paid(con,int(meta.get('order_id') or 0),s.get('id',''))
                elif meta.get('kind')=='driver_tip': _credit_tip(con,s)
    return {'received':True}
