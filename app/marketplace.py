from __future__ import annotations
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, db, now, page, require_user

SHOP_SCHEMA='''
CREATE TABLE IF NOT EXISTS shopping_orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 customer_id INTEGER NOT NULL REFERENCES users(id),
 driver_id INTEGER REFERENCES users(id),
 dropoff TEXT NOT NULL,
 notes TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'posted',
 driver_pay_cents INTEGER NOT NULL DEFAULT 900,
 platform_fee_cents INTEGER NOT NULL DEFAULT 150,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 accepted_at TEXT,
 delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS shopping_stops(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES shopping_orders(id) ON DELETE CASCADE,
 stop_number INTEGER NOT NULL,
 store_name TEXT NOT NULL,
 store_address TEXT DEFAULT '',
 shopping_list TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shopping_orders_status ON shopping_orders(status);
'''

@app.on_event('startup')
def marketplace_startup():
    with db() as con:
        con.executescript(SHOP_SCHEMA)

@app.get('/shop', response_class=HTMLResponse)
def shop_page(request:Request):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}:
        raise HTTPException(403,'Shopping orders are for customer and store accounts.')
    with db() as con:
        orders=con.execute('SELECT o.*,d.name driver FROM shopping_orders o LEFT JOIN users d ON d.id=o.driver_id WHERE o.customer_id=? ORDER BY o.id DESC',(u['id'],)).fetchall()
        stops={}
        for o in orders:
            stops[o['id']]=con.execute('SELECT * FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(o['id'],)).fetchall()
    return page(request,'shop.html',orders=orders,stops=stops)

@app.post('/shop/orders')
def create_shopping_order(
    request:Request,
    dropoff:str=Form(...),
    store1:str=Form(...), address1:str=Form(''), items1:str=Form(...),
    store2:str=Form(''), address2:str=Form(''), items2:str=Form(''),
    store3:str=Form(''), address3:str=Form(''), items3:str=Form(''),
    notes:str=Form('')
):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    raw=[(store1,address1,items1),(store2,address2,items2),(store3,address3,items3)]
    stops=[(s.strip(),a.strip(),i.strip()) for s,a,i in raw if s.strip() and i.strip()]
    if not stops: raise HTTPException(400,'Add at least one store and shopping list.')
    # Independent local marketplace pricing: base shopper pay plus extra-stop pay.
    driver_pay=900 + max(0,len(stops)-1)*350
    fee=150 + max(0,len(stops)-1)*50
    t=now()
    with db() as con:
        cur=con.execute('INSERT INTO shopping_orders(customer_id,dropoff,notes,status,driver_pay_cents,platform_fee_cents,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],dropoff.strip(),notes.strip(),'posted',driver_pay,fee,t,t))
        oid=cur.lastrowid
        for n,(store,address,items) in enumerate(stops,1):
            con.execute('INSERT INTO shopping_stops(order_id,stop_number,store_name,store_address,shopping_list) VALUES(?,?,?,?,?)',(oid,n,store,address,items))
    return RedirectResponse('/shop',303)

@app.get('/driver/shop', response_class=HTMLResponse)
def driver_shop(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        open_orders=con.execute("SELECT o.*,c.name customer FROM shopping_orders o JOIN users c ON c.id=o.customer_id WHERE o.status='posted' ORDER BY o.id DESC").fetchall()
        mine=con.execute("SELECT o.*,c.name customer FROM shopping_orders o JOIN users c ON c.id=o.customer_id WHERE o.driver_id=? AND o.status IN ('accepted','shopping','delivering') ORDER BY o.id DESC",(u['id'],)).fetchall()
        stopmap={}
        for o in list(open_orders)+list(mine):
            stopmap[o['id']]=con.execute('SELECT * FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(o['id'],)).fetchall()
    return page(request,'driver_shop.html',open_orders=open_orders,mine=mine,stopmap=stopmap)

@app.post('/shop/orders/{oid}/accept')
def accept_shopping_order(oid:int,request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        cur=con.execute("UPDATE shopping_orders SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted'",(u['id'],now(),now(),oid))
        if cur.rowcount!=1: raise HTTPException(409,'This shopping order was already claimed.')
    return RedirectResponse('/driver/shop',303)

@app.post('/shop/orders/{oid}/status')
def shopping_order_status(oid:int,request:Request,status:str=Form(...)):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    allowed={'shopping','delivering','delivered'}
    if status not in allowed: raise HTTPException(400,'Invalid status')
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND driver_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        transitions={'accepted':'shopping','shopping':'delivering','delivering':'delivered'}
        if transitions.get(o['status'])!=status: raise HTTPException(400,'Complete the steps in order.')
        delivered_at=now() if status=='delivered' else None
        con.execute('UPDATE shopping_orders SET status=?,updated_at=?,delivered_at=COALESCE(?,delivered_at) WHERE id=?',(status,now(),delivered_at,oid))
    return RedirectResponse('/driver/shop',303)
