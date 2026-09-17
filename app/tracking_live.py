from __future__ import annotations
from datetime import datetime, timezone
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from .main import app, page, require_user
from .database import db
from .routing import geocode_address, OSRM, UA


def _route_points(stops, dropoff):
    points=[]
    for s in stops:
        query=(s['store_address'] or '').strip() or f"{s['store_name']}, Spokane, WA"
        try:
            g=geocode_address(query)
            points.append({'lat':g['lat'],'lon':g['lon'],'label':s['store_name'],'kind':'store'})
        except Exception:
            continue
    try:
        g=geocode_address(dropoff)
        points.append({'lat':g['lat'],'lon':g['lon'],'label':'Customer drop-off','kind':'dropoff'})
    except Exception:
        pass
    return points


def _road_geometry(points):
    if len(points)<2:
        return []
    coords=';'.join(f"{p['lon']},{p['lat']}" for p in points)
    try:
        r=httpx.get(f'{OSRM}/route/v1/driving/{coords}',params={'overview':'full','geometries':'geojson','steps':'false'},headers={'User-Agent':UA},timeout=15)
        r.raise_for_status(); data=r.json()
        if data.get('code')!='Ok' or not data.get('routes'):
            return []
        raw=(data['routes'][0].get('geometry') or {}).get('coordinates') or []
        return [[float(lat),float(lon)] for lon,lat in raw]
    except Exception:
        return []


def _allowed_tracking_user(u, order):
    return u['role']=='admin' or order['customer_id']==u['id'] or (order['driver_id'] and order['driver_id']==u['id'])


@app.on_event('startup')
def live_tracking_startup():
    with db() as con:
        try: con.execute("ALTER TABLE driver_profiles ADD COLUMN location_updated_at TEXT DEFAULT ''")
        except Exception: pass


@app.get('/track/shop/{oid}',response_class=HTMLResponse)
def shopping_tracking_page(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT o.*,dr.name driver FROM shopping_orders o LEFT JOIN users dr ON dr.id=o.driver_id WHERE o.id=?',(oid,)).fetchone()
        if not o: raise HTTPException(404)
        if not _allowed_tracking_user(u,o): raise HTTPException(403)
        stops=con.execute('SELECT stop_number,store_name,store_address,shopping_list FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(oid,)).fetchall()
    points=_route_points(stops,o['dropoff'])
    geometry=_road_geometry(points)
    return page(request,'shopping_tracking.html',order=o,stops=stops,route_points=points,route_geometry=geometry)


@app.get('/api/track/shop/{oid}')
def api_track_shopping(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o: raise HTTPException(404)
        if not _allowed_tracking_user(u,o): raise HTTPException(403)
        p=None
        if o['driver_id'] and o['status'] in {'accepted','shopping','delivering'}:
            p=con.execute('SELECT online,latitude,longitude,location_updated_at FROM driver_profiles WHERE user_id=?',(o['driver_id'],)).fetchone()
    driver=None
    if p and p['latitude'] is not None and p['longitude'] is not None:
        driver={'online':bool(p['online']),'latitude':float(p['latitude']),'longitude':float(p['longitude']),'updated_at':p['location_updated_at'] or ''}
    return JSONResponse({'shopping_order_id':oid,'status':o['status'],'driver':driver})
