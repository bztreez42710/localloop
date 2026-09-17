from __future__ import annotations
from fastapi import Request, Form, HTTPException
from fastapi.responses import JSONResponse
from .main import app, now, require_user
from .database import db
from .mobile_api import _token_user


@app.on_event('startup')
def telemetry_startup():
    with db() as con:
        try: con.execute("ALTER TABLE driver_profiles ADD COLUMN location_updated_at TEXT DEFAULT ''")
        except Exception: pass
        con.execute('''CREATE TABLE IF NOT EXISTS driver_location_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delivery_id INTEGER,
            shopping_order_id INTEGER,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            speed_mph REAL DEFAULT 0,
            bearing REAL DEFAULT 0,
            accuracy_m REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )''')
        try: con.execute('CREATE INDEX IF NOT EXISTS idx_driver_location_delivery ON driver_location_history(delivery_id,id)')
        except Exception: pass
        try: con.execute('CREATE INDEX IF NOT EXISTS idx_driver_location_shop ON driver_location_history(shopping_order_id,id)')
        except Exception: pass


# Replace the original mobile location endpoint with richer active-job telemetry.
for r in list(app.router.routes):
    if getattr(r,'path',None)=='/api/mobile/location' and 'POST' in (getattr(r,'methods',set()) or set()):
        app.router.routes.remove(r)


@app.post('/api/mobile/location')
def mobile_location_telemetry(request:Request,latitude:float=Form(...),longitude:float=Form(...),speed_mps:float=Form(0),bearing:float=Form(0),accuracy_m:float=Form(0)):
    u,_=_token_user(request)
    if not (47.45 <= latitude <= 47.85 and -117.75 <= longitude <= -117.05):
        raise HTTPException(400,'Location is outside the Spokane service area')
    speed_mph=max(0.0,min(float(speed_mps or 0)*2.236936,120.0))
    bearing=max(0.0,min(float(bearing or 0),360.0))
    accuracy=max(0.0,min(float(accuracy_m or 0),1000.0))
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        active_delivery=con.execute("SELECT id FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC LIMIT 1",(u['id'],)).fetchone()
        active_shop=con.execute("SELECT id FROM shopping_orders WHERE driver_id=? AND status IN ('accepted','shopping','delivering') ORDER BY id DESC LIMIT 1",(u['id'],)).fetchone()
        if not p or not p['online'] or not (active_delivery or active_shop):
            raise HTTPException(409,'Location sharing is only available while online with an active job')
        delivery_id=active_delivery['id'] if active_delivery else None
        shopping_id=active_shop['id'] if active_shop else None
        con.execute('UPDATE driver_profiles SET latitude=?,longitude=?,location_updated_at=? WHERE user_id=?',(latitude,longitude,now(),u['id']))
        con.execute('INSERT INTO driver_location_history(driver_id,delivery_id,shopping_order_id,latitude,longitude,speed_mph,bearing,accuracy_m,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(
            u['id'],delivery_id,shopping_id,latitude,longitude,speed_mph,bearing,accuracy,now()
        ))
    return {'ok':True,'speed_mph':round(speed_mph,1),'accuracy_m':round(accuracy,1)}


def _allowed(u, customer_id, driver_id):
    return u['role']=='admin' or customer_id==u['id'] or (driver_id and driver_id==u['id'])


# Replace tracking APIs so customers get a recent movement trail, while speed stays private.
for r in list(app.router.routes):
    p=getattr(r,'path',None); methods=getattr(r,'methods',set()) or set()
    if p in {'/api/track/{did}','/api/track/shop/{oid}'} and 'GET' in methods:
        app.router.routes.remove(r)


@app.get('/api/track/{did}')
def track_delivery_with_trail(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
        if not d: raise HTTPException(404)
        if not _allowed(u,d['customer_id'],d['driver_id']): raise HTTPException(403)
        p=con.execute('SELECT latitude,longitude,location_updated_at FROM driver_profiles WHERE user_id=?',(d['driver_id'],)).fetchone() if d['driver_id'] else None
        rows=con.execute('SELECT latitude,longitude,created_at FROM driver_location_history WHERE delivery_id=? ORDER BY id DESC LIMIT 120',(did,)).fetchall()
    driver=None
    if p and p['latitude'] is not None and p['longitude'] is not None:
        driver={'latitude':float(p['latitude']),'longitude':float(p['longitude']),'updated_at':p['location_updated_at'] or ''}
    trail=[{'latitude':float(r['latitude']),'longitude':float(r['longitude']),'created_at':r['created_at']} for r in reversed(rows)]
    return JSONResponse({'delivery_id':did,'status':d['status'],'driver':driver,'trail':trail})


@app.get('/api/track/shop/{oid}')
def track_shop_with_trail(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o: raise HTTPException(404)
        if not _allowed(u,o['customer_id'],o['driver_id']): raise HTTPException(403)
        p=con.execute('SELECT online,latitude,longitude,location_updated_at FROM driver_profiles WHERE user_id=?',(o['driver_id'],)).fetchone() if o['driver_id'] else None
        rows=con.execute('SELECT latitude,longitude,created_at FROM driver_location_history WHERE shopping_order_id=? ORDER BY id DESC LIMIT 120',(oid,)).fetchall()
    driver=None
    if p and p['latitude'] is not None and p['longitude'] is not None and o['status'] in {'accepted','shopping','delivering'}:
        driver={'online':bool(p['online']),'latitude':float(p['latitude']),'longitude':float(p['longitude']),'updated_at':p['location_updated_at'] or ''}
    trail=[{'latitude':float(r['latitude']),'longitude':float(r['longitude']),'created_at':r['created_at']} for r in reversed(rows)]
    return JSONResponse({'shopping_order_id':oid,'status':o['status'],'driver':driver,'trail':trail})


@app.get('/api/admin/drivers/{uid}/telemetry')
def admin_driver_telemetry(uid:int,request:Request,limit:int=200):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    n=max(1,min(int(limit),500))
    with db() as con:
        rows=con.execute('SELECT delivery_id,shopping_order_id,latitude,longitude,speed_mph,bearing,accuracy_m,created_at FROM driver_location_history WHERE driver_id=? ORDER BY id DESC LIMIT ?',(uid,n)).fetchall()
    return JSONResponse([dict(r) for r in rows])
