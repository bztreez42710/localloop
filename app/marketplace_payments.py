from __future__ import annotations
import uuid
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db
from .payments import configured, finix, FINIX_MERCHANT_ID, FINIX_JS_ENV, FINIX_APPLICATION_ID

@app.get('/pay/market/{oid}',response_class=HTMLResponse)
def market_pay_page(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT o.*,l.title FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.id=? AND o.buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': return RedirectResponse(f'/market/orders/{oid}',303)
    return page(request,'market_payment_portal.html',order=o,processor_ready=configured(),finix_env=FINIX_JS_ENV,finix_application_id=FINIX_APPLICATION_ID)

@app.post('/api/payments/market/{oid}')
async def market_pay_process(oid:int,request:Request):
    u=require_user(request)
    body=await request.json(); token=(body.get('token') or '').strip()
    if not token: raise HTTPException(400,'Missing secure payment token.')
    with db() as con:
        o=con.execute('SELECT o.*,l.quantity available FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.id=? AND o.buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': raise HTTPException(409,'This order is not awaiting payment.')
        if o['quantity']>o['available']: raise HTTPException(409,'The seller no longer has enough quantity available.')
    identity=finix('POST','/identities',json={'entity':{}}); iid=identity.get('id')
    if not iid: raise HTTPException(502,'Could not create buyer record.')
    instrument=finix('POST','/payment_instruments',json={'identity':iid,'token':token,'type':'TOKEN'}); pid=instrument.get('id')
    if not pid: raise HTTPException(502,'Could not create secure payment instrument.')
    transfer=finix('POST','/transfers',json={'amount':o['total_cents'],'currency':'USD','merchant':FINIX_MERCHANT_ID,'source':pid,'idempotency_id':str(uuid.uuid4()),'tags':{'localloop_market_order':str(oid),'type':'marketplace_purchase'}})
    ref=transfer.get('id',''); state=(transfer.get('state') or transfer.get('status') or '').upper()
    funded=state in {'SUCCEEDED','COMPLETED'}; pending=state in {'PENDING','PROCESSING'}
    payment_status='funded' if funded else ('pending' if pending else 'failed')
    with db() as con:
        if funded:
            cur=con.execute('UPDATE marketplace_listings SET quantity=quantity-?,active=CASE WHEN quantity-?<=0 THEN 0 ELSE active END,updated_at=? WHERE id=? AND quantity>=?',(o['quantity'],o['quantity'],now(),o['listing_id'],o['quantity']))
            if cur.rowcount!=1: raise HTTPException(409,'The item sold out before payment completed. Contact LocalLoop for payment review.')
            con.execute("UPDATE marketplace_orders SET payment_provider_ref=?,payment_status='funded',status='paid',updated_at=? WHERE id=?",(ref,now(),oid))
        else:
            con.execute('UPDATE marketplace_orders SET payment_provider_ref=?,payment_status=?,updated_at=? WHERE id=?',(ref,payment_status,now(),oid))
        con.execute('INSERT INTO payment_records(user_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?)',(u['id'],'finix',ref,o['total_cents'],payment_status,'marketplace_purchase',now()))
    return JSONResponse({'ok':funded or pending,'status':payment_status,'reference':ref})

@app.post('/pay/market/{oid}/refresh')
def market_pay_refresh(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=? AND buyer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        ref=o['payment_provider_ref']
    if not ref: raise HTTPException(400,'No payment is pending.')
    transfer=finix('GET',f'/transfers/{ref}'); state=(transfer.get('state') or transfer.get('status') or '').upper()
    funded=state in {'SUCCEEDED','COMPLETED'}; failed=state in {'FAILED','CANCELED','CANCELLED','DECLINED'}
    status='funded' if funded else ('failed' if failed else 'pending')
    with db() as con:
        current=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if funded and current['status']=='awaiting_payment':
            cur=con.execute('UPDATE marketplace_listings SET quantity=quantity-?,active=CASE WHEN quantity-?<=0 THEN 0 ELSE active END,updated_at=? WHERE id=? AND quantity>=?',(current['quantity'],current['quantity'],now(),current['listing_id'],current['quantity']))
            if cur.rowcount==1: con.execute("UPDATE marketplace_orders SET payment_status='funded',status='paid',updated_at=? WHERE id=?",(now(),oid))
        elif failed: con.execute("UPDATE marketplace_orders SET payment_status='failed',updated_at=? WHERE id=?",(now(),oid))
    return RedirectResponse(f'/market/orders/{oid}',303)
