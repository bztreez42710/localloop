from __future__ import annotations
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db

@app.on_event('startup')
def patchpack_startup():
    with db() as con:
        # Useful indexes for the busiest customer/driver queries.
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_deliveries_customer_status ON deliveries(customer_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_driver_status ON deliveries(driver_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_shopping_customer_status ON shopping_orders(customer_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_shopping_driver_status ON shopping_orders(driver_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_shopping_stops_order ON shopping_stops(order_id,stop_number)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id,read_at,id)",
        ):
            try: con.execute(sql)
            except Exception: pass

@app.middleware('http')
async def production_response_headers(request:Request, call_next):
    response=await call_next(request)
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy','camera=(self), geolocation=(self), microphone=()')
    if request.url.path.startswith('/static/'):
        response.headers.setdefault('Cache-Control','public, max-age=86400')
    else:
        response.headers.setdefault('Cache-Control','no-store' if request.url.path.startswith('/api/') else 'private, no-cache')
    return response

@app.get('/healthz')
def healthz():
    return {'ok':True,'service':'localloop'}

@app.get('/readyz')
def readyz():
    try:
        with db() as con:
            con.execute('SELECT 1').fetchone()
        return {'ok':True,'database':'ready'}
    except Exception:
        raise HTTPException(503,'Database is not ready')

@app.get('/notifications',response_class=HTMLResponse)
def notifications_page(request:Request):
    u=require_user(request)
    with db() as con:
        rows=con.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)).fetchall()
        unread=con.execute('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL',(u['id'],)).fetchone()['c']
    return page(request,'notifications.html',notifications=rows,unread=unread)

@app.get('/api/notifications/summary')
def notification_summary(request:Request):
    u=require_user(request)
    with db() as con:
        unread=con.execute('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL',(u['id'],)).fetchone()['c']
        latest=con.execute('SELECT id,title,body,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 5',(u['id'],)).fetchall()
    return JSONResponse({'unread':int(unread),'latest':[dict(r) for r in latest]})

@app.post('/notifications/read-all')
def notifications_read_all(request:Request):
    u=require_user(request)
    with db() as con:
        con.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL',(now(),u['id']))
    return RedirectResponse('/notifications',303)

@app.post('/notifications/{nid}/read')
def notification_read(nid:int,request:Request):
    u=require_user(request)
    with db() as con:
        cur=con.execute('UPDATE notifications SET read_at=? WHERE id=? AND user_id=?',(now(),nid,u['id']))
        if cur.rowcount!=1: raise HTTPException(404)
    return RedirectResponse('/notifications',303)

@app.get('/api/shop/estimate')
def shop_estimate(request:Request,merchandise_dollars:float=0,job_pay_dollars:float=0,stops:int=1,kind:str='standard'):
    require_user(request)
    goods=max(0,min(round(float(merchandise_dollars or 0)*100),150000))
    stops=max(1,min(int(stops or 1),3))
    if kind=='thrift_mystery':
        pay=max(1000,min(round(float(job_pay_dollars or 0)*100),15000))
        fee=max(200,round(pay*.15))
    else:
        pay=900+max(0,stops-1)*350
        fee=150+max(0,stops-1)*50
    return {'merchandise_cents':goods,'shopper_pay_cents':pay,'platform_fee_cents':fee,'total_cents':goods+pay+fee}
