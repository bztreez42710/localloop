from __future__ import annotations
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, page, require_user, now
from .database import db

STATUS_LABELS={
 'awaiting_payment':'Waiting for payment','unfunded':'Not paid yet','funded':'Paid and ready',
 'posted':'Looking for a driver','accepted':'Driver accepted','picked_up':'Picked up','shopping':'Shopping now',
 'delivering':'On the way','delivered':'Delivered','paid':'Paid','completed':'Completed','cancelled':'Cancelled',
 'pending':'Pending review','approved':'Approved','rejected':'Needs attention','created':'Created','failed':'Payment failed'
}

def friendly(v): return STATUS_LABELS.get((v or '').lower(),(v or '').replace('_',' ').title())
def _remove(path,method='GET'):
    for r in list(app.router.routes):
        if getattr(r,'path',None)==path and method in (getattr(r,'methods',set()) or set()): app.router.routes.remove(r)

def _legal_ok(con,u):
    req=[('terms','2026-09-14'),('privacy','2026-09-14')]
    if u['role']=='driver': req.append(('driver_agreement','2026-09-14'))
    try: return all(con.execute('SELECT 1 FROM legal_acceptances WHERE user_id=? AND document_type=? AND version=?',(u['id'],d,v)).fetchone() for d,v in req)
    except Exception: return False

def _verified(con,u):
    if u['role'] not in {'customer','business'}: return True
    try:
        r=con.execute('SELECT verification_status FROM account_verifications WHERE user_id=?',(u['id'],)).fetchone(); return bool(r and r['verification_status']=='verified')
    except Exception: return False

def _onboarded(con,uid):
    try: return bool(con.execute('SELECT 1 FROM onboarding_progress WHERE user_id=? AND completed_at IS NOT NULL',(uid,)).fetchone())
    except Exception: return False

@app.on_event('startup')
def ux_startup():
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS onboarding_progress(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,completed_at TEXT,updated_at TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS marketplace_favorites(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(user_id,listing_id))')
        con.execute('CREATE TABLE IF NOT EXISTS marketplace_recent(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,viewed_at TEXT NOT NULL,PRIMARY KEY(user_id,listing_id))')

@app.get('/onboarding',response_class=HTMLResponse)
def onboarding(request:Request):
    u=require_user(request)
    with db() as con:
        compliance=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone() if u['role']=='driver' else None
        seller=con.execute('SELECT * FROM seller_marketplace_profiles WHERE user_id=?',(u['id'],)).fetchone() if u['role'] in {'customer','business'} else None
    return page(request,'onboarding.html',compliance=compliance,seller=seller)

@app.post('/onboarding/complete')
def onboarding_complete(request:Request):
    u=require_user(request)
    with db() as con:
        con.execute('INSERT INTO onboarding_progress(user_id,completed_at,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET completed_at=excluded.completed_at,updated_at=excluded.updated_at',(u['id'],now(),now()))
    return RedirectResponse('/driver/app' if u['role']=='driver' else '/dashboard',303)

for p,m in [('/dashboard','GET'),('/market','GET'),('/market/listings/{lid}','GET')]: _remove(p,m)

@app.get('/dashboard',response_class=HTMLResponse)
def friendly_dashboard(request:Request):
    u=require_user(request)
    with db() as con:
        if not _legal_ok(con,u): return RedirectResponse('/legal/acceptance',303)
        if not _verified(con,u): return RedirectResponse('/account/verification',303)
        if u['role']=='driver': return RedirectResponse('/driver/app',303)
        staff=con.execute('SELECT staff_role FROM staff_access WHERE user_id=?',(u['id'],)).fetchone()
        if not staff and u['role']!='admin' and not _onboarded(con,u): return RedirectResponse('/onboarding',303)
        if staff:
            stats={'users':con.execute('SELECT COUNT(*) c FROM users').fetchone()['c'],'online':con.execute('SELECT COUNT(*) c FROM driver_profiles WHERE online=1').fetchone()['c'],'active':con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()['c'],'today':con.execute("SELECT COUNT(*) c FROM deliveries WHERE date(created_at)=date('now')").fetchone()['c'],'disputes':con.execute("SELECT COUNT(*) c FROM disputes WHERE status='open'").fetchone()['c']}; rows=con.execute('SELECT d.*,u.name customer,dr.name driver FROM deliveries d JOIN users u ON u.id=d.customer_id LEFT JOIN users dr ON dr.id=d.driver_id ORDER BY d.id DESC LIMIT 40').fetchall(); return page(request,'staff.html',staff_role=staff['staff_role'],stats=stats,deliveries=rows)
        if u['role']=='admin':
            stats={'users':con.execute('SELECT COUNT(*) c FROM users').fetchone()['c'],'online':con.execute('SELECT COUNT(*) c FROM driver_profiles WHERE online=1').fetchone()['c'],'active':con.execute("SELECT COUNT(*) c FROM deliveries WHERE status IN ('posted','accepted','picked_up')").fetchone()['c'],'today':con.execute("SELECT COUNT(*) c FROM deliveries WHERE date(created_at)=date('now')").fetchone()['c'],'revenue':con.execute("SELECT COALESCE(SUM(platform_fee_cents),0) c FROM deliveries WHERE status='delivered'").fetchone()['c'],'disputes':con.execute("SELECT COUNT(*) c FROM disputes WHERE status='open'").fetchone()['c']}
            alerts={'accounts':con.execute("SELECT COUNT(*) c FROM users u LEFT JOIN account_verifications a ON a.user_id=u.id WHERE u.role IN ('customer','business') AND COALESCE(a.verification_status,'pending')!='verified'").fetchone()['c'],'drivers':con.execute("SELECT COUNT(*) c FROM users u LEFT JOIN driver_compliance c ON c.user_id=u.id WHERE u.role='driver' AND (COALESCE(c.identity_status,'pending')!='approved' OR COALESCE(c.background_status,'pending')!='approved' OR COALESCE(c.insurance_status,'pending')!='approved')").fetchone()['c'],'reports':con.execute("SELECT COUNT(*) c FROM marketplace_reports WHERE status='open'").fetchone()['c'],'payments':con.execute("SELECT COUNT(*) c FROM payment_records WHERE status IN ('failed','error')").fetchone()['c']}
            rows=con.execute('SELECT d.*,u.name customer,dr.name driver FROM deliveries d JOIN users u ON u.id=d.customer_id LEFT JOIN users dr ON dr.id=d.driver_id ORDER BY d.id DESC LIMIT 50').fetchall(); return page(request,'admin.html',stats=stats,alerts=alerts,deliveries=rows,friendly=friendly)
        deliveries=con.execute('SELECT d.*,dr.name driver FROM deliveries d LEFT JOIN users dr ON dr.id=d.driver_id WHERE d.customer_id=? OR d.business_id=? ORDER BY d.id DESC',(u['id'],u['id'])).fetchall(); return page(request,'customer.html',deliveries=deliveries,account_verified=True,friendly=friendly)

@app.get('/orders',response_class=HTMLResponse)
def unified_orders(request:Request):
    u=require_user(request)
    with db() as con:
        deliveries=con.execute('SELECT d.*,dr.name driver FROM deliveries d LEFT JOIN users dr ON dr.id=d.driver_id WHERE d.customer_id=? OR d.business_id=? ORDER BY d.id DESC',(u['id'],u['id'])).fetchall() if u['role']!='driver' else con.execute('SELECT d.*,c.name customer FROM deliveries d JOIN users c ON c.id=d.customer_id WHERE d.driver_id=? ORDER BY d.id DESC',(u['id'],)).fetchall()
        shopping=con.execute('SELECT * FROM shopping_orders WHERE customer_id=? ORDER BY id DESC',(u['id'],)).fetchall() if u['role']!='driver' else con.execute('SELECT * FROM shopping_orders WHERE driver_id=? ORDER BY id DESC',(u['id'],)).fetchall()
        market=con.execute('SELECT o.*,l.title FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.buyer_id=? OR o.seller_id=? ORDER BY o.id DESC',(u['id'],u['id'])).fetchall()
    return page(request,'orders.html',deliveries=deliveries,shopping=shopping,market_orders=market,friendly=friendly)

@app.get('/market',response_class=HTMLResponse)
def market_browse(request:Request,q:str='',category:str='',min_price:float=0,max_price:float=0):
    u=None
    try: u=require_user(request)
    except Exception: pass
    sql="SELECT l.*,u.name seller_name,p.display_name,p.home_based,p.state_code FROM marketplace_listings l JOIN users u ON u.id=l.seller_id LEFT JOIN seller_marketplace_profiles p ON p.user_id=l.seller_id WHERE l.active=1 AND l.quantity>0"; args=[]
    if q.strip(): sql+=' AND (LOWER(l.title) LIKE ? OR LOWER(l.description) LIKE ?)'; s='%'+q.strip().lower()+'%'; args += [s,s]
    if category.strip(): sql+=' AND l.category=?'; args.append(category.strip().lower())
    if min_price>0: sql+=' AND l.price_cents>=?'; args.append(round(min_price*100))
    if max_price>0: sql+=' AND l.price_cents<=?'; args.append(round(max_price*100))
    sql+=' ORDER BY l.id DESC LIMIT 100'
    with db() as con:
        rows=con.execute(sql,args).fetchall(); fav=set()
        recent=[]
        if u:
            fav={r['listing_id'] for r in con.execute('SELECT listing_id FROM marketplace_favorites WHERE user_id=?',(u['id'],)).fetchall()}
            recent=con.execute('SELECT l.* FROM marketplace_recent r JOIN marketplace_listings l ON l.id=r.listing_id WHERE r.user_id=? AND l.active=1 ORDER BY r.viewed_at DESC LIMIT 6',(u['id'],)).fetchall()
    return page(request,'market_home.html',listings=rows,favorites=fav,recent=recent,q=q,category=category,min_price=min_price,max_price=max_price)

@app.get('/market/listings/{lid}',response_class=HTMLResponse)
def market_listing_friendly(lid:int,request:Request):
    u=None
    try:u=require_user(request)
    except Exception:pass
    with db() as con:
        l=con.execute("SELECT l.*,u.name seller_name,p.display_name,p.home_based,p.state_code,p.contact_email,p.return_default_days,p.inform_verified FROM marketplace_listings l JOIN users u ON u.id=l.seller_id LEFT JOIN seller_marketplace_profiles p ON p.user_id=l.seller_id WHERE l.id=? AND l.active=1",(lid,)).fetchone()
        if not l: raise HTTPException(404)
        if u: con.execute('INSERT INTO marketplace_recent(user_id,listing_id,viewed_at) VALUES(?,?,?) ON CONFLICT(user_id,listing_id) DO UPDATE SET viewed_at=excluded.viewed_at',(u['id'],lid,now()))
    return page(request,'market_listing.html',listing=l,seller_stats={'transactions':0,'gross_cents':0,'high_volume':False})

@app.post('/market/favorites/{lid}')
def favorite(lid:int,request:Request):
    u=require_user(request)
    with db() as con:
        exists=con.execute('SELECT 1 FROM marketplace_favorites WHERE user_id=? AND listing_id=?',(u['id'],lid)).fetchone()
        if exists: con.execute('DELETE FROM marketplace_favorites WHERE user_id=? AND listing_id=?',(u['id'],lid))
        else: con.execute('INSERT INTO marketplace_favorites(user_id,listing_id,created_at) VALUES(?,?,?)',(u['id'],lid,now()))
    return RedirectResponse(request.headers.get('referer') or '/market',303)

@app.get('/market/store/{uid}',response_class=HTMLResponse)
def storefront(uid:int,request:Request):
    with db() as con:
        seller=con.execute('SELECT u.id,u.name,u.created_at,p.* FROM users u LEFT JOIN seller_marketplace_profiles p ON p.user_id=u.id WHERE u.id=?',(uid,)).fetchone()
        if not seller: raise HTTPException(404)
        listings=con.execute('SELECT * FROM marketplace_listings WHERE seller_id=? AND active=1 AND quantity>0 ORDER BY id DESC',(uid,)).fetchall()
        completed=con.execute("SELECT COUNT(*) c FROM marketplace_orders WHERE seller_id=? AND status='completed'",(uid,)).fetchone()['c']
    return page(request,'storefront.html',seller=seller,listings=listings,completed=completed)
