from __future__ import annotations
import uuid
from fastapi import Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from .main import app, require_user, now
from .database import db
from .payments import finix, configured

@app.post('/admin/refunds/{oid}/issue')
def issue_marketplace_refund(oid:int,request:Request,refund_amount_cents:int=Form(0)):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if not configured(): raise HTTPException(503,'Finix live/sandbox credentials are not connected yet, so LocalLoop cannot move refund funds automatically.')
    with db() as con:
        req=con.execute('SELECT * FROM marketplace_refund_requests WHERE order_id=?',(oid,)).fetchone()
        order=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not req or not order: raise HTTPException(404)
        if req['status']=='resolved': raise HTTPException(409,'This refund request is already resolved.')
        if not order['payment_provider_ref']: raise HTTPException(400,'This order has no Finix transfer reference.')
        amount=refund_amount_cents or order['total_cents']
        if amount<1 or amount>order['total_cents']: raise HTTPException(400,'Refund amount must be between $0.01 and the amount paid.')
        existing=con.execute("SELECT * FROM payment_records WHERE kind='marketplace_refund' AND marketplace_order_id=? AND status IN ('pending','funded','succeeded','completed')",(oid,)).fetchone() if _has_column(con,'payment_records','marketplace_order_id') else None
        if existing: raise HTTPException(409,'A refund for this order is already recorded as pending or completed.')
    reversal=finix('POST',f"/transfers/{order['payment_provider_ref']}/reversals",json={
        'refund_amount':amount,'idempotency_id':f'localloop-refund-{oid}-{uuid.uuid4().hex}',
        'tags':{'localloop_order':str(oid),'type':'marketplace_refund'}
    })
    state=(reversal.get('state') or reversal.get('status') or 'PENDING').lower(); ref=reversal.get('id','')
    resolved=state in {'succeeded','completed'}
    with db() as con:
        _ensure_marketplace_order_column(con)
        con.execute('INSERT INTO payment_records(user_id,provider,provider_ref,amount_cents,status,kind,marketplace_order_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(order['buyer_id'],'finix',ref,-amount,state,'marketplace_refund',oid,now()))
        con.execute('UPDATE marketplace_refund_requests SET status=?,updated_at=? WHERE order_id=?',('resolved' if resolved else 'reviewed',now(),oid))
        if resolved: con.execute("UPDATE marketplace_orders SET status='refunded',payment_status='refunded',updated_at=? WHERE id=?",(now(),oid))
    return RedirectResponse('/admin/refunds',303)

def _has_column(con,table,name):
    try: return any(r['name']==name for r in con.execute(f'PRAGMA table_info({table})').fetchall())
    except Exception: return False

def _ensure_marketplace_order_column(con):
    if not _has_column(con,'payment_records','marketplace_order_id'):
        try: con.execute('ALTER TABLE payment_records ADD COLUMN marketplace_order_id INTEGER')
        except Exception: pass
