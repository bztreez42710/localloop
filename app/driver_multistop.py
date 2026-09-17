from __future__ import annotations
from fastapi import Request, Form, HTTPException
from .main import app, now
from .database import db
from .mobile_api import _token_user
from . import driver_experience as de

_base_current=de._current_job_payload

@app.on_event('startup')
def multistop_startup():
    with db() as con:
        try: con.execute('ALTER TABLE shopping_route_preferences ADD COLUMN current_index INTEGER DEFAULT 0')
        except Exception: pass


def _multistop_current(con,uid:int):
    kind,row=de._active_job(con,uid)
    if not row or kind!='shopping': return _base_current(con,uid)
    if row['status']=='delivering': return _base_current(con,uid)
    p=con.execute('SELECT latitude,longitude,location_updated_at FROM driver_profiles WHERE user_id=?',(uid,)).fetchone()
    lat=float(p['latitude']) if p and p['latitude'] is not None else None; lon=float(p['longitude']) if p and p['longitude'] is not None else None
    stops=de._shop_stops(con,row['id'])
    pref=con.execute('SELECT mode,current_index FROM shopping_route_preferences WHERE shopping_order_id=?',(row['id'],)).fetchone()
    mode=pref['mode'] if pref else 'optimized'; idx=int(pref['current_index'] or 0) if pref else 0
    ordered=de._optimized_stops(stops,lat,lon) if mode=='optimized' else [dict(x) for x in stops]
    if not ordered: return _base_current(con,uid)
    idx=max(0,min(idx,len(ordered)-1)); current=ordered[idx]
    address=current.get('resolved_address') or current.get('store_address') or f"{current.get('store_name','')}, Spokane, WA"
    dlat=current.get('lat'); dlon=current.get('lon')
    if dlat is None:
        try:
            g=de.geocode_address(address); dlat,dlon=g['lat'],g['lon']; address=g['display_name']
        except Exception: pass
    eta=distance=None; arrived=False
    if lat is not None and dlat is not None:
        distance,eta=de._route_from_coords(lat,lon,dlat,dlon); arrived=de._haversine(lat,lon,dlat,dlon)<=0.12
    gps_age=de._gps_age(p['location_updated_at'] if p else '')
    rows=con.execute('SELECT segment_miles FROM driver_location_history WHERE driver_id=? AND shopping_order_id=?',(uid,row['id'])).fetchall()
    miles=round(sum(float(r['segment_miles'] or 0) for r in rows),2)
    stage='Arriving' if arrived else ('Heading to store' if row['status']=='accepted' else 'Shopping')
    return {'kind':'shopping','id':row['id'],'status':row['status'],'stage':stage,'next_label':current.get('store_name') or f'Store {idx+1}','next_address':address,'notes':row['notes'] or '','expected_earnings_cents':int(row['driver_pay_cents']),'eta_minutes':eta,'distance_to_next_miles':distance,'arrived':arrived,'gps_age_seconds':gps_age,'gps_stale':gps_age is None or gps_age>45,'route_deviation':False,'trip_miles':miles,'stop_count':len(ordered),'current_stop_index':idx,'has_more_stops':idx<len(ordered)-1,'route_mode':mode}

# Driver experience functions resolve this module global at request time, so patching
# the helper upgrades both the native cockpit and the customer live ETA endpoint.
de._current_job_payload=_multistop_current

@app.post('/api/mobile/shopping/{oid}/next-stop')
def next_shopping_stop(oid:int,request:Request):
    u,_=_token_user(request)
    with db() as con:
        o=con.execute("SELECT * FROM shopping_orders WHERE id=? AND driver_id=? AND status='shopping'",(oid,u['id'])).fetchone()
        if not o: raise HTTPException(409,'Shopping must be active before moving to the next store.')
        stops=de._shop_stops(con,oid)
        if len(stops)<2: return {'ok':True,'current_index':0,'has_more_stops':False}
        con.execute('INSERT OR IGNORE INTO shopping_route_preferences(shopping_order_id,driver_id,mode,stop_order,updated_at,current_index) VALUES(?,?,?,?,?,0)',(oid,u['id'],'optimized','[]',now()))
        pref=con.execute('SELECT current_index FROM shopping_route_preferences WHERE shopping_order_id=?',(oid,)).fetchone(); idx=int(pref['current_index'] or 0)
        if idx>=len(stops)-1: return {'ok':True,'current_index':idx,'has_more_stops':False}
        idx+=1; con.execute('UPDATE shopping_route_preferences SET current_index=?,updated_at=? WHERE shopping_order_id=?',(idx,now(),oid))
    return {'ok':True,'current_index':idx,'has_more_stops':idx<len(stops)-1}
