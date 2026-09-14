from __future__ import annotations
import secrets
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, page, require_user, now, notify, event
from .database import db
from .production import _safe_image


def _add_column(con, table, definition):
    try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
    except Exception: pass


def _remove(path, method='POST'):
    for r in list(app.router.routes):
        if getattr(r, 'path', None) == path and method in (getattr(r, 'methods', set()) or set()):
            app.router.routes.remove(r)


@app.on_event('startup')
def community_plus_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS trusted_connections(
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            connection_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id,target_user_id,connection_type)
        )''')
        _add_column(con,'deliveries',"handoff_code TEXT DEFAULT ''")
        _add_column(con,'deliveries','handoff_required INTEGER DEFAULT 0')
        con.execute('''CREATE TABLE IF NOT EXISTS marketplace_refund_requests(
            order_id INTEGER PRIMARY KEY REFERENCES marketplace_orders(id) ON DELETE CASCADE,
            buyer_id INTEGER NOT NULL REFERENCES users(id),
            reason TEXT DEFAULT '', status TEXT DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')


@app.get('/connections', response_class=HTMLResponse)
def connections(request:Request):
    u=require_user(request)
    with db() as con:
        sellers=con.execute("SELECT u.id,u.name,p.display_name,p.state_code FROM trusted_connections t JOIN users u ON u.id=t.target_user_id LEFT JOIN seller_marketplace_profiles p ON p.user_id=u.id WHERE t.user_id=? AND t.connection_type='seller' ORDER BY t.created_at DESC",(u['id'],)).fetchall()
        drivers=con.execute("SELECT u.id,u.name,d.rating,d.completed FROM trusted_connections t JOIN users u ON u.id=t.target_user_id LEFT JOIN driver_profiles d ON d.user_id=u.id WHERE t.user_id=? AND t.connection_type='driver' ORDER BY t.created_at DESC",(u['id'],)).fetchall()
        eligible=con.execute("SELECT DISTINCT u.id,u.name,d.rating,d.completed FROM deliveries x JOIN users u ON u.id=x.driver_id LEFT JOIN driver_profiles d ON d.user_id=u.id WHERE x.customer_id=? AND x.status='delivered' AND x.driver_id IS NOT NULL ORDER BY u.name",(u['id'],)).fetchall() if u['role'] in {'customer','business','admin'} else []
    return page(request,'connections.html',sellers=sellers,drivers=drivers,eligible_drivers=eligible)


@app.post('/connections/seller/{uid}/toggle')
def toggle_seller_connection(uid:int,request:Request):
    u=require_user(request)
    if uid==u['id']: raise HTTPException(400,'You cannot follow yourself.')
    with db() as con:
        if not con.execute('SELECT 1 FROM seller_marketplace_profiles WHERE user_id=?',(uid,)).fetchone(): raise HTTPException(404)
        row=con.execute("SELECT 1 FROM trusted_connections WHERE user_id=? AND target_user_id=? AND connection_type='seller'",(u['id'],uid)).fetchone()
        if row: con.execute("DELETE FROM trusted_connections WHERE user_id=? AND target_user_id=? AND connection_type='seller'",(u['id'],uid))
        else: con.execute("INSERT INTO trusted_connections(user_id,target_user_id,connection_type,created_at) VALUES(?,?,?,?)",(u['id'],uid,'seller',now()))
    return RedirectResponse(request.headers.get('referer') or '/connections',303)


@app.post('/connections/driver/{uid}/toggle')
def toggle_driver_connection(uid:int,request:Request):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    with db() as con:
        if not con.execute("SELECT 1 FROM deliveries WHERE customer_id=? AND driver_id=? AND status='delivered'",(u['id'],uid)).fetchone():
            raise HTTPException(403,'A driver can be added to your trusted people after a completed delivery with them.')
        row=con.execute("SELECT 1 FROM trusted_connections WHERE user_id=? AND target_user_id=? AND connection_type='driver'",(u['id'],uid)).fetchone()
        if row: con.execute("DELETE FROM trusted_connections WHERE user_id=? AND target_user_id=? AND connection_type='driver'",(u['id'],uid))
        else: con.execute("INSERT INTO trusted_connections(user_id,target_user_id,connection_type,created_at) VALUES(?,?,?,?)",(u['id'],uid,'driver',now()))
    return RedirectResponse('/connections',303)


@app.get('/deliveries/{did}/handoff', response_class=HTMLResponse)
def handoff_page(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT d.*,dr.name driver_name FROM deliveries d LEFT JOIN users dr ON dr.id=d.driver_id WHERE d.id=?',(did,)).fetchone()
        if not d or (u['role']!='admin' and d['customer_id']!=u['id']): raise HTTPException(404)
    return page(request,'handoff.html',delivery=d)


@app.post('/deliveries/{did}/handoff/enable')
def enable_handoff(did:int,request:Request):
    u=require_user(request)
    code=f'{secrets.randbelow(10000):04d}'
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
        if not d or (u['role']!='admin' and d['customer_id']!=u['id']): raise HTTPException(404)
        if d['status'] in {'delivered','cancelled'}: raise HTTPException(400,'This delivery is already closed.')
        con.execute('UPDATE deliveries SET handoff_required=1,handoff_code=?,updated_at=? WHERE id=?',(code,now(),did))
    return RedirectResponse(f'/deliveries/{did}/handoff',303)


@app.post('/deliveries/{did}/handoff/disable')
def disable_handoff(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
        if not d or (u['role']!='admin' and d['customer_id']!=u['id']): raise HTTPException(404)
        con.execute("UPDATE deliveries SET handoff_required=0,handoff_code='',updated_at=? WHERE id=?",(now(),did))
    return RedirectResponse(f'/deliveries/{did}/handoff',303)


_remove('/deliveries/{did}/status','POST')
@app.post('/deliveries/{did}/status')
def delivery_status_with_handoff(did:int,request:Request,status:str=Form(...),proof:str=Form(''),proof_photo:str=Form(''),handoff_code:str=Form('')):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    photo=_safe_image(proof_photo) if proof_photo else ''
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=? AND driver_id=?',(did,u['id'])).fetchone()
        if not d: raise HTTPException(404)
        if status=='picked_up' and d['status']=='accepted':
            con.execute("UPDATE deliveries SET status='picked_up',picked_up_at=?,updated_at=? WHERE id=?",(now(),now(),did))
        elif status=='delivered' and d['status']=='picked_up':
            if d['handoff_required'] and (handoff_code or '').strip()!=d['handoff_code']:
                raise HTTPException(400,'The customer handoff code is required and did not match.')
            con.execute("UPDATE deliveries SET status='delivered',proof=?,proof_photo=?,handoff_code='',delivered_at=?,updated_at=? WHERE id=?",(proof.strip(),photo,now(),now(),did))
            con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(d['driver_pay_cents'],u['id']))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)',(u['id'],did,'driver_earning',d['driver_pay_cents'],'Delivery earning',now()))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,?,?,?,?,?)',(did,'platform_fee',d['platform_fee_cents'],'Platform revenue',now()))
        else: raise HTTPException(400,'Complete the delivery steps in order.')
        event(con,did,u['id'],status)
        notify(con,d['customer_id'],'Delivery update',f"Delivery #{did}: {status.replace('_',' ')}")
    return RedirectResponse('/dashboard',303)


_remove('/market/orders/{oid}/cancel','POST')
@app.post('/market/orders/{oid}/cancel')
def safer_market_cancel(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id']!=o['buyer_id']): raise HTTPException(404)
        if o['status']=='awaiting_payment':
            con.execute("UPDATE marketplace_orders SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
        elif o['status'] in {'paid','ready_for_handoff'}:
            raise HTTPException(400,'This order has already been paid. Use Request refund so LocalLoop can track the refund instead of cancelling a paid order.')
        else: raise HTTPException(400,'This order cannot be cancelled here.')
    return RedirectResponse(f'/market/orders/{oid}',303)


@app.post('/market/orders/{oid}/refund-request')
def market_refund_request(oid:int,request:Request,reason:str=Form('')):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not o or u['id']!=o['buyer_id']: raise HTTPException(404)
        if o['status'] not in {'paid','ready_for_handoff'}: raise HTTPException(400,'A refund request is available after payment and before completion.')
        con.execute('INSERT OR REPLACE INTO marketplace_refund_requests(order_id,buyer_id,reason,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',(oid,u['id'],reason.strip()[:1500],'open',now(),now()))
    return RedirectResponse(f'/market/orders/{oid}',303)


@app.post('/market/orders/{oid}/ready')
def market_ready(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id']!=o['seller_id']): raise HTTPException(404)
        if o['status']!='paid': raise HTTPException(400,'Only a paid order can be marked ready.')
        con.execute("UPDATE marketplace_orders SET status='ready_for_handoff',updated_at=? WHERE id=?",(now(),oid))
    return RedirectResponse(f'/market/orders/{oid}',303)


@app.post('/market/orders/{oid}/received')
def market_received(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id']!=o['buyer_id']): raise HTTPException(404)
        if o['status']!='ready_for_handoff': raise HTTPException(400,'The seller has not marked this order ready yet.')
        con.execute("UPDATE marketplace_orders SET status='completed',completed_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
    return RedirectResponse(f'/market/orders/{oid}',303)


@app.get('/admin/refunds',response_class=HTMLResponse)
def admin_refunds(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        rows=con.execute("SELECT r.*,o.total_cents,o.payment_provider_ref,l.title,b.name buyer_name,s.name seller_name FROM marketplace_refund_requests r JOIN marketplace_orders o ON o.id=r.order_id JOIN marketplace_listings l ON l.id=o.listing_id JOIN users b ON b.id=o.buyer_id JOIN users s ON s.id=o.seller_id ORDER BY CASE WHEN r.status='open' THEN 0 ELSE 1 END,r.updated_at DESC").fetchall()
    return page(request,'admin_refunds.html',refunds=rows)


@app.post('/admin/refunds/{oid}/status')
def admin_refund_status(oid:int,request:Request,status:str=Form(...)):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if status not in {'open','reviewed','resolved'}: raise HTTPException(400)
    with db() as con:
        r=con.execute('SELECT * FROM marketplace_refund_requests WHERE order_id=?',(oid,)).fetchone()
        if not r: raise HTTPException(404)
        con.execute('UPDATE marketplace_refund_requests SET status=?,updated_at=? WHERE order_id=?',(status,now(),oid))
    return RedirectResponse('/admin/refunds',303)
