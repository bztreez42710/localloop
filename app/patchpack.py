from __future__ import annotations
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db

@app.on_event('startup')
def patchpack_startup():
    with db() as con:
        # Useful indexes for the busiest customer/driver queries.
        con.execute('''CREATE TABLE IF NOT EXISTS provider_ratings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            driver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delivery_id INTEGER,
            shopping_order_id INTEGER,
            stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )''')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_rating_delivery ON provider_ratings(customer_id,delivery_id) WHERE delivery_id IS NOT NULL')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_rating_shop ON provider_ratings(customer_id,shopping_order_id) WHERE shopping_order_id IS NOT NULL')
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


def _refresh_driver_rating(con,driver_id:int):
    row=con.execute('SELECT AVG(stars) avg_rating,COUNT(*) c FROM provider_ratings WHERE driver_id=?',(driver_id,)).fetchone()
    if row and row['c']:
        con.execute('UPDATE driver_profiles SET rating=? WHERE user_id=?',(round(float(row['avg_rating']),2),driver_id))

@app.post('/rate/{kind}/{oid}')
def rate_provider(kind:str,oid:int,request:Request,stars:int=Form(...),comment:str=Form('')):
    u=require_user(request)
    if kind not in {'delivery','shopping'}: raise HTTPException(404)
    if int(stars)<1 or int(stars)>5: raise HTTPException(400,'Rating must be between 1 and 5 stars.')
    table='deliveries' if kind=='delivery' else 'shopping_orders'
    id_col='delivery_id' if kind=='delivery' else 'shopping_order_id'
    with db() as con:
        row=con.execute(f'SELECT * FROM {table} WHERE id=?',(oid,)).fetchone()
        if not row or row['customer_id']!=u['id'] or row['status']!='delivered' or not row['driver_id']:
            raise HTTPException(404)
        existing=con.execute(f'SELECT id FROM provider_ratings WHERE customer_id=? AND {id_col}=?',(u['id'],oid)).fetchone()
        if existing:
            con.execute('UPDATE provider_ratings SET stars=?,comment=?,created_at=? WHERE id=?',(int(stars),comment.strip()[:1000],now(),existing['id']))
        else:
            con.execute(
                'INSERT INTO provider_ratings(customer_id,driver_id,delivery_id,shopping_order_id,stars,comment,created_at) VALUES(?,?,?,?,?,?,?)',
                (u['id'],row['driver_id'],oid if kind=='delivery' else None,oid if kind=='shopping' else None,int(stars),comment.strip()[:1000],now())
            )
        _refresh_driver_rating(con,row['driver_id'])
        notify(con,row['driver_id'],'New customer rating',f'You received a {int(stars)}-star rating.')
    return RedirectResponse('/'+('track/'+str(oid) if kind=='delivery' else 'track/shop/'+str(oid))+'?rated=1',303)
