from __future__ import annotations
import re, base64
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
 status TEXT NOT NULL DEFAULT 'awaiting_payment',
 driver_pay_cents INTEGER NOT NULL DEFAULT 900,
 platform_fee_cents INTEGER NOT NULL DEFAULT 150,
 estimated_goods_cents INTEGER DEFAULT 0,
 actual_goods_cents INTEGER DEFAULT 0,
 payment_provider_ref TEXT DEFAULT '',
 payment_status TEXT DEFAULT 'unfunded',
 receipt_photo TEXT DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 accepted_at TEXT,
 delivered_at TEXT,
 cancelled_at TEXT
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

def is_spokane(address:str)->bool:
    a=(address or '').lower(); z=re.search(r'\b(\d{5})\b',a)
    if z and not z.group(1).startswith('992'): return False
    return ('spokane' in a and ('wa' in a or 'washington' in a)) or bool(z and z.group(1).startswith('992'))

def safe_photo(data:str,max_bytes:int=1_750_000)->str:
    if not data: return ''
    if not data.startswith('data:image/'): raise HTTPException(400,'Receipt must be an image.')
    try: raw=base64.b64decode(data.split(',',1)[1],validate=False)
    except Exception: raise HTTPException(400,'Could not read receipt photo.')
    if len(raw)>max_bytes: raise HTTPException(400,'Receipt photo is too large.')
    return data

@app.on_event('startup')
def marketplace_startup():
    with db() as con:
        con.executescript(SHOP_SCHEMA)
        cols={r['name'] for r in con.execute('PRAGMA table_info(shopping_orders)').fetchall()}
        migrations={'cancelled_at':'TEXT','estimated_goods_cents':'INTEGER DEFAULT 0','actual_goods_cents':'INTEGER DEFAULT 0','payment_provider_ref':"TEXT DEFAULT ''",'payment_status':"TEXT DEFAULT 'unfunded'",'receipt_photo':"TEXT DEFAULT ''"}
        for name,definition in migrations.items():
            if name not in cols: con.execute(f'ALTER TABLE shopping_orders ADD COLUMN {name} {definition}')

@app.get('/shop', response_class=HTMLResponse)
def shop_page(request:Request):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403,'Shopping orders are for customer and store accounts.')
    with db() as con:
        orders=con.execute('SELECT o.*,d.name driver FROM shopping_orders o LEFT JOIN users d ON d.id=o.driver_id WHERE o.customer_id=? ORDER BY o.id DESC',(u['id'],)).fetchall()
        stops={o['id']:con.execute('SELECT * FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(o['id'],)).fetchall() for o in orders}
    return page(request,'shop.html',orders=orders,stops=stops)

@app.post('/shop/orders')
def create_shopping_order(request:Request,dropoff:str=Form(...),store1:str=Form(...),address1:str=Form(''),items1:str=Form(...),store2:str=Form(''),address2:str=Form(''),items2:str=Form(''),store3:str=Form(''),address3:str=Form(''),items3:str=Form(''),estimated_goods_dollars:float=Form(...),notes:str=Form('')):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    if not is_spokane(dropoff): raise HTTPException(400,'LocalLoop shopping currently delivers only to Spokane, Washington (992xx).')
    if estimated_goods_dollars<0 or estimated_goods_dollars>1500: raise HTTPException(400,'Estimated merchandise must be between $0 and $1,500 for the Spokane pilot.')
    raw=[(store1,address1,items1),(store2,address2,items2),(store3,address3,items3)]
    stops=[(s.strip(),a.strip(),i.strip()) for s,a,i in raw if s.strip() and i.strip()]
    if not stops: raise HTTPException(400,'Add at least one store and shopping list.')
    for _,addr,_ in stops:
        if addr and not is_spokane(addr): raise HTTPException(400,'Every store address must be in Spokane, Washington (992xx).')
    driver_pay=900 + max(0,len(stops)-1)*350; fee=150 + max(0,len(stops)-1)*50; goods=round(estimated_goods_dollars*100); t=now()
    with db() as con:
        cur=con.execute('INSERT INTO shopping_orders(customer_id,dropoff,notes,status,driver_pay_cents,platform_fee_cents,estimated_goods_cents,payment_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(u['id'],dropoff.strip(),notes.strip(),'awaiting_payment',driver_pay,fee,goods,'unfunded',t,t)); oid=cur.lastrowid
        for n,(store,address,items) in enumerate(stops,1): con.execute('INSERT INTO shopping_stops(order_id,stop_number,store_name,store_address,shopping_list) VALUES(?,?,?,?,?)',(oid,n,store,address,items))
    return RedirectResponse('/shop',303)

@app.post('/shop/orders/{oid}/cancel')
def cancel_shopping_order(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o or (o['customer_id']!=u['id'] and u['role']!='admin'): raise HTTPException(404)
        if o['status'] not in {'awaiting_payment','posted'}: raise HTTPException(400,'This shopping request can no longer be cancelled here.')
        con.execute("UPDATE shopping_orders SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
    return RedirectResponse('/shop',303)

@app.get('/driver/shop', response_class=HTMLResponse)
def driver_shop(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        open_orders=con.execute("SELECT o.*,c.name customer FROM shopping_orders o JOIN users c ON c.id=o.customer_id WHERE o.status='posted' AND o.payment_status='funded' ORDER BY o.id DESC").fetchall()
        mine=con.execute("SELECT o.*,c.name customer FROM shopping_orders o JOIN users c ON c.id=o.customer_id WHERE o.driver_id=? AND o.status IN ('accepted','shopping','delivering') ORDER BY o.id DESC",(u['id'],)).fetchall()
        stopmap={}
        for o in list(open_orders)+list(mine): stopmap[o['id']]=con.execute('SELECT * FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(o['id'],)).fetchall()
    return page(request,'driver_shop.html',open_orders=open_orders,mine=mine,stopmap=stopmap)

@app.post('/shop/orders/{oid}/accept')
def accept_shopping_order(oid:int,request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if not c or any(c[k]!='approved' for k in ('identity_status','background_status','insurance_status')): raise HTTPException(403,'Complete driver verification before accepting shopping work.')
        cur=con.execute("UPDATE shopping_orders SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted' AND payment_status='funded'",(u['id'],now(),now(),oid))
        if cur.rowcount!=1: raise HTTPException(409,'This shopping order is not funded or was already claimed.')
    return RedirectResponse('/driver/shop',303)

@app.post('/shop/orders/{oid}/status')
def shopping_order_status(oid:int,request:Request,status:str=Form(...),actual_goods_dollars:float=Form(0),receipt_photo:str=Form('')):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    if status not in {'shopping','delivering','delivered'}: raise HTTPException(400,'Invalid status')
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND driver_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        transitions={'accepted':'shopping','shopping':'delivering','delivering':'delivered'}
        if transitions.get(o['status'])!=status: raise HTTPException(400,'Complete the steps in order.')
        if status=='delivering':
            actual=round(actual_goods_dollars*100)
            if actual<0: raise HTTPException(400,'Receipt total cannot be negative.')
            if actual>o['estimated_goods_cents']: raise HTTPException(400,'Receipt is above the customer-funded merchandise limit. Do not charge the customer more without additional approval.')
            photo=safe_photo(receipt_photo)
            if not photo: raise HTTPException(400,'Take a receipt photo before leaving the stores.')
            con.execute("UPDATE shopping_orders SET status='delivering',actual_goods_cents=?,receipt_photo=?,updated_at=? WHERE id=?",(actual,photo,now(),oid))
        elif status=='delivered':
            con.execute("UPDATE shopping_orders SET status='delivered',delivered_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
            payout=o['driver_pay_cents']+o['actual_goods_cents']
            con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(payout,u['id']))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,NULL,?,?,?,?)',(u['id'],'shopping_reimbursement',payout,f'Shopping order #{oid}: merchandise reimbursement + driver pay',now()))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,NULL,?,?,?,?)',('platform_fee',o['platform_fee_cents'],f'Shopping order #{oid}',now()))
            con.execute("INSERT INTO payment_records(user_id,shopping_order_id,provider,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?)",(u['id'],oid,'finix',payout,'pending','shopper_payout',now()))
        else: con.execute("UPDATE shopping_orders SET status='shopping',updated_at=? WHERE id=?",(now(),oid))
    return RedirectResponse('/driver/shop',303)

@app.get('/shopping/ops', response_class=HTMLResponse)
def shopping_ops(request:Request):
    u=require_user(request)
    with db() as con:
        staff=con.execute('SELECT staff_role FROM staff_access WHERE user_id=?',(u['id'],)).fetchone()
        if u['role']!='admin' and not staff: raise HTTPException(403)
        rows=con.execute('SELECT o.*,c.name customer,d.name driver FROM shopping_orders o JOIN users c ON c.id=o.customer_id LEFT JOIN users d ON d.id=o.driver_id ORDER BY o.id DESC LIMIT 100').fetchall()
        stopmap={o['id']:con.execute('SELECT * FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(o['id'],)).fetchall() for o in rows}
    return page(request,'shopping_ops.html',orders=rows,stopmap=stopmap)
