from __future__ import annotations
from functools import lru_cache
from urllib.parse import quote as urlquote
import httpx
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from .main import app, db, now, page, require_user, quote, event

UA='LocalLoop-Spokane/0.2 (+https://localloop-app.onrender.com)'
NOMINATIM='https://nominatim.openstreetmap.org'
OSRM='https://router.project-osrm.org'

@lru_cache(maxsize=512)
def geocode_address(address:str):
    q=(address or '').strip()
    if not q: raise ValueError('Missing address')
    params={'q':q,'format':'jsonv2','limit':1,'countrycodes':'us','addressdetails':1,'viewbox':'-117.70,47.85,-117.05,47.40','bounded':1}
    r=httpx.get(NOMINATIM+'/search',params=params,headers={'User-Agent':UA},timeout=12)
    r.raise_for_status(); data=r.json()
    if not data: raise ValueError('Address not found in the Spokane service area')
    x=data[0]; a=x.get('address',{}); lat=float(x['lat']); lon=float(x['lon'])
    city=(a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or '').lower()
    postcode=(a.get('postcode') or '')
    if 'spokane' not in city and not postcode.startswith('992'):
        raise ValueError('Address is outside Spokane')
    return {'lat':lat,'lon':lon,'display_name':x.get('display_name',q),'postcode':postcode}

@lru_cache(maxsize=512)
def route_info(pickup:str,dropoff:str):
    a=geocode_address(pickup); b=geocode_address(dropoff)
    url=f"{OSRM}/route/v1/driving/{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
    r=httpx.get(url,params={'overview':'full','geometries':'geojson','steps':'false'},headers={'User-Agent':UA},timeout=15)
    r.raise_for_status(); data=r.json()
    if data.get('code')!='Ok' or not data.get('routes'): raise ValueError('No driving route found')
    rt=data['routes'][0]
    return {'pickup':a,'dropoff':b,'distance_miles':round(float(rt['distance'])/1609.344,2),'duration_minutes':round(float(rt['duration'])/60,1),'geometry':rt.get('geometry')}

def _account_ready(u):
    if u['role']=='admin': return True
    if u['role'] not in {'customer','business'}: return False
    with db() as con:
        try:
            r=con.execute("SELECT verification_status FROM account_verifications WHERE user_id=?",(u['id'],)).fetchone()
            return bool(r and r['verification_status']=='verified')
        except Exception:
            return False

@app.get('/api/address/verify')
def verify_address(q:str,request:Request):
    u=require_user(request)
    if u['role'] in {'customer','business'} and not _account_ready(u): return JSONResponse({'ok':False,'error':'Complete account verification before creating a delivery.'},status_code=403)
    try: return JSONResponse({'ok':True,**geocode_address(q)})
    except Exception as e: return JSONResponse({'ok':False,'error':str(e)},status_code=400)

@app.get('/api/route')
def api_route(pickup:str,dropoff:str,request:Request):
    u=require_user(request)
    if u['role'] in {'customer','business'} and not _account_ready(u): return JSONResponse({'ok':False,'error':'Complete account verification before creating a delivery.'},status_code=403)
    try: return JSONResponse({'ok':True,**route_info(pickup,dropoff)})
    except Exception as e: return JSONResponse({'ok':False,'error':str(e)},status_code=400)

for r in list(app.router.routes):
    if getattr(r,'path',None)=='/deliveries' and 'POST' in (getattr(r,'methods',set()) or set()): app.router.routes.remove(r)

@app.post('/deliveries')
def create_routed_delivery(request:Request,pickup:str=Form(...),dropoff:str=Form(...),item_description:str=Form(...),distance_miles:float=Form(1),notes:str=Form('')):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    if u['role'] in {'customer','business'} and not _account_ready(u): return RedirectResponse('/account/verification',303)
    try: rt=route_info(pickup,dropoff)
    except Exception as e: raise HTTPException(400,f'Please enter valid Spokane pickup and drop-off addresses: {e}')
    miles=rt['distance_miles']; q=quote(miles); t=now()
    with db() as con:
        cur=con.execute('INSERT INTO deliveries(customer_id,business_id,pickup,dropoff,item_description,distance_miles,quoted_cents,platform_fee_cents,driver_pay_cents,notes,created_at,updated_at,pickup_lat,pickup_lng,dropoff_lat,dropoff_lng) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(u['id'],u['id'] if u['role']=='business' else None,rt['pickup']['display_name'],rt['dropoff']['display_name'],item_description.strip(),miles,q['customer_total_cents'],q['platform_fee_cents'],q['driver_pay_cents'],notes.strip(),t,t,rt['pickup']['lat'],rt['pickup']['lon'],rt['dropoff']['lat'],rt['dropoff']['lon']))
        event(con,cur.lastrowid,u['id'],'posted',{'distance_miles':miles,'duration_minutes':rt['duration_minutes'],**q})
    return RedirectResponse('/dashboard',303)

@app.get('/track/{did}',response_class=HTMLResponse)
def tracking_page(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT d.*,dr.name driver FROM deliveries d LEFT JOIN users dr ON dr.id=d.driver_id WHERE d.id=?',(did,)).fetchone()
        if not d: raise HTTPException(404)
        if u['role']!='admin' and d['customer_id']!=u['id'] and d['driver_id']!=u['id']: raise HTTPException(403)
    return page(request,'tracking.html',delivery=d)

@app.get('/navigate/{did}',response_class=HTMLResponse)
def navigation_page(did:int,request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con: d=con.execute('SELECT * FROM deliveries WHERE id=? AND driver_id=?',(did,u['id'])).fetchone()
    if not d: raise HTTPException(404)
    return page(request,'navigation.html',delivery=d)
