from __future__ import annotations
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from .main import app, require_user, page
from .database import db
from .mobile_api import _token_user


def _trip_rows(con,uid:int,limit:int=30):
    rows=con.execute('''SELECT delivery_id,shopping_order_id,MIN(created_at) started_at,MAX(created_at) ended_at,
        COALESCE(SUM(segment_miles),0) miles,COALESCE(MAX(speed_mph),0) max_speed_mph,
        COALESCE(AVG(accuracy_m),0) avg_accuracy_m,COUNT(*) points
        FROM driver_location_history WHERE driver_id=? GROUP BY delivery_id,shopping_order_id ORDER BY ended_at DESC LIMIT ?''',(uid,limit)).fetchall()
    return [dict(r) for r in rows]

@app.get('/api/mobile/trips')
def mobile_trip_history(request:Request):
    u,_=_token_user(request)
    with db() as con:
        trips=_trip_rows(con,u['id'],40)
        incidents=[dict(r) for r in con.execute('SELECT id,delivery_id,shopping_order_id,kind,details,created_at,resolved_at FROM driver_incidents WHERE driver_id=? ORDER BY id DESC LIMIT 30',(u['id'],)).fetchall()]
    return {'trips':trips,'incidents':incidents}

@app.get('/api/admin/drivers/{uid}/trip-history')
def admin_trip_history(uid:int,request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        driver=con.execute('SELECT id,name,email FROM users WHERE id=? AND role=\'driver\'',(uid,)).fetchone()
        if not driver: raise HTTPException(404)
        trips=_trip_rows(con,uid,100)
        incidents=[dict(r) for r in con.execute('SELECT * FROM driver_incidents WHERE driver_id=? ORDER BY id DESC LIMIT 100',(uid,)).fetchall()]
    return JSONResponse({'driver':dict(driver),'trips':trips,'incidents':incidents})

@app.get('/admin/driver-safety',response_class=HTMLResponse)
def admin_driver_safety(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        drivers=con.execute('''SELECT u.id,u.name,u.email,d.online,d.completed,d.location_updated_at,
            (SELECT COUNT(*) FROM driver_incidents i WHERE i.driver_id=u.id AND COALESCE(i.resolved_at,'')='') open_incidents
            FROM users u LEFT JOIN driver_profiles d ON d.user_id=u.id WHERE u.role='driver' ORDER BY u.name''').fetchall()
        incidents=con.execute('''SELECT i.*,u.name driver FROM driver_incidents i JOIN users u ON u.id=i.driver_id ORDER BY i.id DESC LIMIT 100''').fetchall()
    return page(request,'admin_driver_safety.html',drivers=drivers,incidents=incidents)
