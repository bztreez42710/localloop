from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db

@app.get('/market/sales-report',response_class=HTMLResponse)
def seller_sales_report(request:Request):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    with db() as con:
        profile=con.execute('SELECT * FROM seller_marketplace_profiles WHERE user_id=?',(u['id'],)).fetchone()
        rows=con.execute("SELECT o.*,l.title FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.seller_id=? AND o.status IN ('paid','completed') ORDER BY o.created_at DESC",(u['id'],)).fetchall()
    months=defaultdict(lambda:{'orders':0,'gross_cents':0,'fees_cents':0})
    for r in rows:
        month=(r['created_at'] or '')[:7] or 'unknown'
        months[month]['orders']+=1; months[month]['gross_cents']+=r['item_cents']; months[month]['fees_cents']+=r['platform_fee_cents']
    monthly=[{'month':m,**v} for m,v in sorted(months.items(),reverse=True)]
    return page(request,'market_sales_report.html',profile=profile,monthly=monthly,orders=rows)

@app.post('/market/seller-certify')
def seller_annual_certify(request:Request):
    u=require_user(request)
    if u['role'] not in {'customer','business','admin'}: raise HTTPException(403)
    with db() as con:
        p=con.execute('SELECT * FROM seller_marketplace_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p: raise HTTPException(404,'Create your seller profile first.')
        con.execute('UPDATE seller_marketplace_profiles SET annual_certified_at=?,updated_at=? WHERE user_id=?',(now(),now(),u['id']))
    return RedirectResponse('/market/sales-report',303)
