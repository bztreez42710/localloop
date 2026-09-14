from __future__ import annotations
from datetime import datetime, timezone, timedelta
import re
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, now, page, require_user
from .database import db

ALLOWED_CATEGORIES={'general','home','electronics','clothing','pet_supplies','hobby','books','garden','other'}
PROHIBITED_TERMS={
    'gun','firearm','ammo','ammunition','silencer','suppressor','switchblade','taser','brass knuckles',
    'cannabis','marijuana','thc','weed','cbd','mushroom','psilocybin','cocaine','meth','fentanyl','heroin',
    'cigarette','cigar','vape','nicotine','tobacco','beer','wine','vodka','whiskey','liquor',
    'prescription','oxycodone','adderall','xanax','steroid','dnp','firework','explosive','poison',
    'stolen','counterfeit','fake designer','porn','sex toy','vibrator','dildo','gambling'
}

SCHEMA='''
CREATE TABLE IF NOT EXISTS seller_marketplace_profiles(
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 display_name TEXT DEFAULT '', seller_kind TEXT DEFAULT 'individual', home_based INTEGER DEFAULT 1,
 contact_email TEXT DEFAULT '', contact_phone TEXT DEFAULT '', state_code TEXT DEFAULT 'WA',
 return_default_days INTEGER DEFAULT 0, inform_verified INTEGER DEFAULT 0,
 annual_certified_at TEXT DEFAULT '', seller_attested_at TEXT DEFAULT '', suspended INTEGER DEFAULT 0,
 updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS marketplace_listings(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
 condition_text TEXT NOT NULL DEFAULT 'used', price_cents INTEGER NOT NULL,
 quantity INTEGER NOT NULL DEFAULT 1, new_unused INTEGER DEFAULT 0,
 return_days INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marketplace_orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id),
 buyer_id INTEGER NOT NULL REFERENCES users(id), seller_id INTEGER NOT NULL REFERENCES users(id),
 quantity INTEGER NOT NULL DEFAULT 1, item_cents INTEGER NOT NULL,
 platform_fee_cents INTEGER NOT NULL DEFAULT 100, total_cents INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'awaiting_payment', payment_status TEXT DEFAULT 'unfunded',
 payment_provider_ref TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 completed_at TEXT, cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS marketplace_reports(
 id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
 reporter_user_id INTEGER REFERENCES users(id), reason TEXT NOT NULL, details TEXT DEFAULT '', status TEXT DEFAULT 'open', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_active ON marketplace_listings(active,created_at);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_seller ON marketplace_orders(seller_id,status);
CREATE INDEX IF NOT EXISTS idx_marketplace_reports_status ON marketplace_reports(status);
'''

def _clean(v:str)->str: return (v or '').strip()
def _blocked(text:str)->bool:
    t=re.sub(r'[^a-z0-9 ]+',' ',(text or '').lower())
    return any(term in t for term in PROHIBITED_TERMS)

def _seller_stats(con,uid:int):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=365)).isoformat()
    row=con.execute("SELECT COUNT(*) n,COALESCE(SUM(item_cents),0) gross FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.seller_id=? AND o.status='completed' AND l.new_unused=1 AND o.completed_at>=?",(uid,cutoff)).fetchone()
    return {'transactions':row['n'],'gross_cents':row['gross'],'high_volume':row['n']>=200 and row['gross']>=500000}

def _profile(con,uid:int):
    return con.execute('SELECT * FROM seller_marketplace_profiles WHERE user_id=?',(uid,)).fetchone()

@app.on_event('startup')
def community_marketplace_startup():
    with db() as con: con.executescript(SCHEMA)

@app.get('/market',response_class=HTMLResponse)
def market_home(request:Request):
    with db() as con:
        rows=con.execute("SELECT l.*,u.name seller_name,p.display_name,p.home_based,p.state_code,p.contact_email FROM marketplace_listings l JOIN users u ON u.id=l.seller_id LEFT JOIN seller_marketplace_profiles p ON p.user_id=l.seller_id WHERE l.active=1 AND l.quantity>0 ORDER BY l.id DESC LIMIT 100").fetchall()
    return page(request,'market_home.html',listings=rows)

@app.get('/market/listings/{lid}',response_class=HTMLResponse)
def market_listing(lid:int,request:Request):
    with db() as con:
        l=con.execute("SELECT l.*,u.name seller_name,p.display_name,p.home_based,p.state_code,p.contact_email,p.return_default_days,p.inform_verified FROM marketplace_listings l JOIN users u ON u.id=l.seller_id LEFT JOIN seller_marketplace_profiles p ON p.user_id=l.seller_id WHERE l.id=? AND l.active=1",(lid,)).fetchone()
        if not l: raise HTTPException(404)
        stats=_seller_stats(con,l['seller_id'])
    return page(request,'market_listing.html',listing=l,seller_stats=stats)

@app.get('/market/sell',response_class=HTMLResponse)
def seller_center(request:Request):
    u=require_user(request)
    if u['role'] not in {'business','customer','admin'}: raise HTTPException(403,'Seller tools are available to customer, store, and owner accounts.')
    with db() as con:
        p=_profile(con,u['id'])
        rows=con.execute('SELECT * FROM marketplace_listings WHERE seller_id=? ORDER BY id DESC',(u['id'],)).fetchall()
        stats=_seller_stats(con,u['id'])
        orders=con.execute('SELECT o.*,l.title FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id WHERE o.seller_id=? ORDER BY o.id DESC LIMIT 50',(u['id'],)).fetchall()
    return page(request,'seller_center.html',profile=p,listings=rows,seller_stats=stats,orders=orders)

@app.post('/market/seller-profile')
def seller_profile_save(request:Request,display_name:str=Form(...),seller_kind:str=Form('individual'),home_based:int=Form(1),contact_email:str=Form(...),contact_phone:str=Form(''),state_code:str=Form('WA'),return_default_days:int=Form(0),attest:str=Form('')):
    u=require_user(request)
    if u['role'] not in {'business','customer','admin'}: raise HTTPException(403)
    if attest!='yes': raise HTTPException(400,'You must attest that your listings are lawful and accurately described.')
    if seller_kind not in {'individual','business'}: raise HTTPException(400,'Invalid seller type.')
    if '@' not in contact_email: raise HTTPException(400,'A working seller email is required.')
    if return_default_days not in {0,7,14,30}: raise HTTPException(400,'Choose an available return window.')
    state=_clean(state_code).upper()[:2]
    with db() as con:
        p=_profile(con,u['id'])
        if p:
            con.execute('UPDATE seller_marketplace_profiles SET display_name=?,seller_kind=?,home_based=?,contact_email=?,contact_phone=?,state_code=?,return_default_days=?,seller_attested_at=?,updated_at=? WHERE user_id=?',(_clean(display_name),seller_kind,1 if home_based else 0,_clean(contact_email).lower(),_clean(contact_phone),state,return_default_days,now(),now(),u['id']))
        else:
            con.execute('INSERT INTO seller_marketplace_profiles(user_id,display_name,seller_kind,home_based,contact_email,contact_phone,state_code,return_default_days,seller_attested_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(u['id'],_clean(display_name),seller_kind,1 if home_based else 0,_clean(contact_email).lower(),_clean(contact_phone),state,return_default_days,now(),now()))
    return RedirectResponse('/market/sell',303)

@app.post('/market/listings')
def create_listing(request:Request,title:str=Form(...),description:str=Form(...),category:str=Form(...),condition_text:str=Form('used'),price_dollars:float=Form(...),quantity:int=Form(1),new_unused:int=Form(0),return_days:int=Form(0),lawful_attestation:str=Form('')):
    u=require_user(request)
    if lawful_attestation!='yes': raise HTTPException(400,'Confirm that you own or may lawfully sell the item and that the listing is accurate.')
    title=_clean(title); description=_clean(description); category=_clean(category).lower(); condition_text=_clean(condition_text).lower()
    if category not in ALLOWED_CATEGORIES: raise HTTPException(400,'That category is not supported.')
    if _blocked(title+' '+description): raise HTTPException(400,'This listing appears to contain an item LocalLoop does not allow. See the Prohibited Items Policy.')
    if condition_text not in {'new','like new','used','refurbished','for parts'}: raise HTTPException(400,'Choose a supported condition.')
    if price_dollars<0.50 or price_dollars>10000 or quantity<1 or quantity>1000: raise HTTPException(400,'Check price and quantity.')
    if return_days not in {0,7,14,30}: raise HTTPException(400,'Choose an available return window.')
    with db() as con:
        p=_profile(con,u['id'])
        if not p or not p['seller_attested_at']: raise HTTPException(403,'Complete your seller profile first.')
        if p['suspended']: raise HTTPException(403,'Selling access is suspended.')
        stats=_seller_stats(con,u['id'])
        if stats['high_volume'] and new_unused and not p['inform_verified']:
            raise HTTPException(403,'Your new/unused sales reached the federal high-volume threshold. Complete seller verification before posting more new/unused products.')
        cents=round(price_dollars*100); t=now()
        con.execute('INSERT INTO marketplace_listings(seller_id,title,description,category,condition_text,price_cents,quantity,new_unused,return_days,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u['id'],title,description,category,condition_text,cents,quantity,1 if new_unused else 0,return_days,t,t))
    return RedirectResponse('/market/sell',303)

@app.post('/market/listings/{lid}/toggle')
def toggle_listing(lid:int,request:Request):
    u=require_user(request)
    with db() as con:
        l=con.execute('SELECT * FROM marketplace_listings WHERE id=?',(lid,)).fetchone()
        if not l or (l['seller_id']!=u['id'] and u['role']!='admin'): raise HTTPException(404)
        con.execute('UPDATE marketplace_listings SET active=?,updated_at=? WHERE id=?',(0 if l['active'] else 1,now(),lid))
    return RedirectResponse('/market/sell',303)

@app.post('/market/listings/{lid}/buy')
def buy_listing(lid:int,request:Request,quantity:int=Form(1)):
    u=require_user(request)
    if u['role']=='driver': raise HTTPException(403,'Use a customer or store account to buy marketplace goods.')
    with db() as con:
        l=con.execute('SELECT * FROM marketplace_listings WHERE id=? AND active=1',(lid,)).fetchone()
        if not l: raise HTTPException(404)
        if l['seller_id']==u['id']: raise HTTPException(400,'You cannot buy your own listing.')
        if quantity<1 or quantity>l['quantity']: raise HTTPException(400,'Requested quantity is unavailable.')
        item=l['price_cents']*quantity; fee=max(50,round(item*0.05)); total=item+fee; t=now()
        cur=con.execute('INSERT INTO marketplace_orders(listing_id,buyer_id,seller_id,quantity,item_cents,platform_fee_cents,total_cents,status,payment_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(lid,u['id'],l['seller_id'],quantity,item,fee,total,'awaiting_payment','unfunded',t,t))
        oid=cur.lastrowid
    return RedirectResponse(f'/market/orders/{oid}',303)

@app.get('/market/orders/{oid}',response_class=HTMLResponse)
def marketplace_order_page(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT o.*,l.title,l.return_days,s.name seller_name,b.name buyer_name FROM marketplace_orders o JOIN marketplace_listings l ON l.id=o.listing_id JOIN users s ON s.id=o.seller_id JOIN users b ON b.id=o.buyer_id WHERE o.id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id'] not in {o['buyer_id'],o['seller_id']}): raise HTTPException(404)
    return page(request,'market_order.html',order=o)

@app.post('/market/orders/{oid}/cancel')
def cancel_market_order(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM marketplace_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id']!=o['buyer_id']): raise HTTPException(404)
        if o['status'] not in {'awaiting_payment','paid'}: raise HTTPException(400,'This order cannot be cancelled here.')
        con.execute("UPDATE marketplace_orders SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
    return RedirectResponse(f'/market/orders/{oid}',303)

@app.post('/market/listings/{lid}/report')
def report_listing(lid:int,request:Request,reason:str=Form(...),details:str=Form('')):
    reason=_clean(reason); details=_clean(details)[:2000]
    if reason not in {'suspected_stolen','counterfeit','unsafe','prohibited','misleading','other'}: raise HTTPException(400,'Choose a report reason.')
    uid=None
    try: uid=require_user(request)['id']
    except Exception: uid=None
    with db() as con:
        if not con.execute('SELECT 1 FROM marketplace_listings WHERE id=?',(lid,)).fetchone(): raise HTTPException(404)
        con.execute('INSERT INTO marketplace_reports(listing_id,reporter_user_id,reason,details,status,created_at) VALUES(?,?,?,?,?,?)',(lid,uid,reason,details,'open',now()))
    return RedirectResponse(f'/market/listings/{lid}?reported=1',303)

@app.get('/admin/marketplace-compliance',response_class=HTMLResponse)
def marketplace_compliance(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        sellers=con.execute("SELECT u.id,u.name,u.email,p.* FROM users u LEFT JOIN seller_marketplace_profiles p ON p.user_id=u.id WHERE p.user_id IS NOT NULL ORDER BY u.id DESC").fetchall()
        reports=con.execute("SELECT r.*,l.title,u.name seller_name FROM marketplace_reports r JOIN marketplace_listings l ON l.id=r.listing_id JOIN users u ON u.id=l.seller_id WHERE r.status='open' ORDER BY r.id DESC").fetchall()
        stats={s['id']:_seller_stats(con,s['id']) for s in sellers}
    return page(request,'market_compliance.html',sellers=sellers,reports=reports,seller_stats=stats)

@app.post('/admin/marketplace-sellers/{uid}/review')
def marketplace_seller_review(uid:int,request:Request,inform_verified:int=Form(0),suspended:int=Form(0)):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        p=_profile(con,uid)
        if not p: raise HTTPException(404)
        con.execute('UPDATE seller_marketplace_profiles SET inform_verified=?,suspended=?,annual_certified_at=?,updated_at=? WHERE user_id=?',(1 if inform_verified else 0,1 if suspended else 0,now() if inform_verified else p['annual_certified_at'],now(),uid))
    return RedirectResponse('/admin/marketplace-compliance',303)

@app.post('/admin/marketplace-reports/{rid}/resolve')
def marketplace_report_resolve(rid:int,request:Request,action:str=Form('dismiss')):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if action not in {'dismiss','remove_listing'}: raise HTTPException(400)
    with db() as con:
        r=con.execute('SELECT * FROM marketplace_reports WHERE id=?',(rid,)).fetchone()
        if not r: raise HTTPException(404)
        if action=='remove_listing': con.execute('UPDATE marketplace_listings SET active=0,updated_at=? WHERE id=?',(now(),r['listing_id']))
        con.execute("UPDATE marketplace_reports SET status='resolved' WHERE id=?",(rid,))
    return RedirectResponse('/admin/marketplace-compliance',303)
