from __future__ import annotations
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db
from .payments import configured, stripe, _create_checkout, _session_paid

@app.get('/pay/market/{oid}',response_class=HTMLResponse)
def market_pay_page(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT o.*,l.title FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.id=? AND o.buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': return RedirectResponse(f'/market/orders/{oid}',303)
    return page(request,'market_payment_portal.html',order=o,processor_ready=configured())

@app.post('/pay/market/{oid}/stripe/start')
def market_stripe_start(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT o.*,l.title,l.quantity available FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.id=? AND o.buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': raise HTTPException(409,'This order is not awaiting payment.')
        if o['quantity']>o['available']: raise HTTPException(409,'The seller no longer has enough quantity available.')
    s=_create_checkout(request,o['total_cents'],f'LocalLoop marketplace order #{oid}: {o["title"]}','marketplace',oid)
    with db() as con:
        con.execute("UPDATE marketplace_orders SET payment_provider_ref=?,payment_status='pending',updated_at=? WHERE id=?",(s.get('id',''),now(),oid))
        con.execute('INSERT INTO payment_records(user_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?)',(u['id'],'stripe',s.get('id',''),o['total_cents'],'pending','marketplace_purchase',now()))
    return RedirectResponse(s['url'],303)

@app.get('/pay/market/{oid}/stripe/success')
def market_stripe_success(oid:int,request:Request,session_id:str):
    u=require_user(request)
    s=stripe('GET',f'/checkout/sessions/{session_id}')
    meta=s.get('metadata') or {}
    if str(meta.get('order_id'))!=str(oid) or meta.get('kind')!='marketplace': raise HTTPException(400,'Payment session does not match this order.')
    paid=_session_paid(s)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=? AND buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if paid and o['status']=='awaiting_payment':
            cur=con.execute('UPDATE marketplace_listings SET quantity=quantity-?,active=CASE WHEN quantity-?<=0 THEN 0 ELSE active END,updated_at=? WHERE id=? AND quantity>=?',(o['quantity'],o['quantity'],now(),o['listing_id'],o['quantity']))
            if cur.rowcount!=1: raise HTTPException(409,'The item sold out before payment completed. Contact LocalLoop for payment review.')
            con.execute("UPDATE marketplace_orders SET payment_provider_ref=?,payment_status='funded',status='paid',updated_at=? WHERE id=?",(session_id,now(),oid))
            con.execute("UPDATE payment_records SET status='funded' WHERE provider_ref=?",(session_id,))
        else:
            con.execute("UPDATE marketplace_orders SET payment_provider_ref=?,payment_status='pending',updated_at=? WHERE id=?",(session_id,now(),oid))
    return RedirectResponse(f'/market/orders/{oid}',303)

from . import marketplace_reporting  # noqa: E402,F401
