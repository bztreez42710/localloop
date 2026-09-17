from __future__ import annotations
from urllib.parse import quote
from functools import lru_cache
import base64, struct, zlib, binascii
from fastapi import Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from .main import app, db, page, user_from_request, now, event, notify

STATUS_LABELS={'posted':'Looking for a driver','accepted':'Driver accepted','picked_up':'Picked up','delivered':'Delivered','cancelled':'Cancelled'}
def friendly(v): return STATUS_LABELS.get((v or '').lower(),(v or '').replace('_',' ').title())
def _driver(request):
    u=user_from_request(request)
    if not u or u['role']!='driver': return None
    return u

def _safe_image(data:str,max_bytes:int=1_750_000)->str:
    if not data: return ''
    if not data.startswith('data:image/'): raise HTTPException(400,'Photo proof must be an image.')
    try: raw=base64.b64decode(data.split(',',1)[1],validate=False)
    except Exception: raise HTTPException(400,'Could not read photo proof.')
    if len(raw)>max_bytes: raise HTTPException(400,'Photo is too large. Please use a smaller image.')
    return data

@lru_cache(maxsize=4)
def _icon_png(size:int)->bytes:
    def chunk(kind:bytes,data:bytes)->bytes:
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',binascii.crc32(kind+data)&0xffffffff)
    raw=bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            lime=(183,255,60,255); dark=(7,17,31,255); purple=(109,93,252,255)
            left=size*.29<=x<=size*.43 and size*.23<=y<=size*.75; bottom=size*.29<=x<=size*.72 and size*.62<=y<=size*.75
            dx=x-size*.72; dy=y-size*.29; dot=(dx*dx+dy*dy)<=(size*.09)**2
            raw.extend(purple if dot else (dark if left or bottom else lime))
    sig=b'\x89PNG\r\n\x1a\n'; ihdr=struct.pack('>IIBBBBB',size,size,8,6,0,0,0)
    return sig+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(bytes(raw),9))+chunk(b'IEND',b'')

for r in list(app.router.routes):
    p=getattr(r,'path',None); methods=getattr(r,'methods',set()) or set()
    if p in {'/deliveries/{did}/accept','/deliveries/{did}/status'} and 'POST' in methods: app.router.routes.remove(r)

@app.get('/driver/app', response_class=HTMLResponse)
def driver_app(request:Request):
    u=_driver(request)
    if not u: return RedirectResponse('/driver/login?next='+quote('/driver/app',safe=''),303)
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_profiles(user_id) VALUES(?)',(u['id'],))
        profile=con.execute('SELECT * FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        available=con.execute("SELECT d.*,u.name customer FROM deliveries d JOIN users u ON u.id=d.customer_id WHERE d.status='posted' ORDER BY d.id DESC LIMIT 40").fetchall()
        shopping_available=con.execute("SELECT o.*,c.name customer,(SELECT COUNT(*) FROM shopping_stops s WHERE s.order_id=o.id) stop_count FROM shopping_orders o JOIN users c ON c.id=o.customer_id WHERE o.status='posted' AND o.payment_status='funded' ORDER BY o.id DESC LIMIT 40").fetchall()
        mine=con.execute("SELECT * FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC",(u['id'],)).fetchall()
        compliance=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'driver_app.html',profile=profile,available=available,shopping_available=shopping_available,mine=mine,compliance=compliance,friendly=friendly,notice=request.query_params.get('notice',''))

@app.get('/driver/offers.json')
def driver_offers(request:Request):
    u=_driver(request)
    if not u: raise HTTPException(401)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p or not p['online']: return JSONResponse({'online':False,'offers':[]},headers={'Cache-Control':'no-store'})
        rows=con.execute("SELECT id,driver_pay_cents,distance_miles,pickup,dropoff,item_description,updated_at FROM deliveries WHERE status='posted' ORDER BY id DESC LIMIT 40").fetchall()
        shopping=con.execute("SELECT o.id,o.driver_pay_cents,o.dropoff,o.updated_at,(SELECT COUNT(*) FROM shopping_stops s WHERE s.order_id=o.id) stop_count FROM shopping_orders o WHERE o.status='posted' AND o.payment_status='funded' ORDER BY o.id DESC LIMIT 40").fetchall()
        offers=[{'key':'delivery:'+str(x['id']),'type':'delivery',**dict(x)} for x in rows]
        offers += [{'key':'shopping:'+str(x['id']),'type':'shopping','id':x['id'],'driver_pay_cents':x['driver_pay_cents'],'distance_miles':0,'pickup':f"{x['stop_count']} shopping stop"+('s' if x['stop_count']!=1 else ''),'dropoff':x['dropoff'],'item_description':'Personal shopping request','updated_at':x['updated_at']} for x in shopping]
    return JSONResponse({'online':True,'offers':offers},headers={'Cache-Control':'no-store'})

@app.post('/deliveries/{did}/accept')
def driver_accept_delivery(did:int,request:Request):
    u=_driver(request)
    if not u: return RedirectResponse('/driver/login?next='+quote('/driver/app',safe=''),303)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone(); c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        ready=bool(c and c['identity_status']=='approved' and c['background_status']=='approved' and c['insurance_status']=='approved')
        if not ready:return RedirectResponse('/driver/setup?verification_required=1',303)
        if not p or not p['online']:return RedirectResponse('/driver/app?notice=go_online#offers',303)
        cur=con.execute("UPDATE deliveries SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted'",(u['id'],now(),now(),did))
        if cur.rowcount!=1:return RedirectResponse('/driver/app?notice=already_claimed#offers',303)
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone(); event(con,did,u['id'],'accepted'); notify(con,d['customer_id'],'Driver assigned',f'Delivery #{did} was accepted.')
    return RedirectResponse('/driver/app#active',303)

@app.post('/deliveries/{did}/status')
def driver_delivery_status(did:int,request:Request,status:str=Form(...),proof:str=Form(''),proof_photo:str=Form(''),handoff_code:str=Form('')):
    u=_driver(request)
    if not u:return RedirectResponse('/driver/login?next='+quote('/driver/app',safe=''),303)
    photo=_safe_image(proof_photo) if proof_photo else ''
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=? AND driver_id=?',(did,u['id'])).fetchone()
        if not d:return RedirectResponse('/driver/app?notice=delivery_missing#active',303)
        if status=='picked_up' and d['status']=='accepted':con.execute("UPDATE deliveries SET status='picked_up',picked_up_at=?,updated_at=? WHERE id=?",(now(),now(),did))
        elif status=='delivered' and d['status']=='picked_up':
            con.execute("UPDATE deliveries SET status='delivered',proof=?,proof_photo=?,delivered_at=?,updated_at=? WHERE id=?",(proof.strip(),photo,now(),now(),did)); con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(d['driver_pay_cents'],u['id'])); con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)',(u['id'],did,'driver_earning',d['driver_pay_cents'],'Delivery earning',now())); con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,?,?,?,?,?)',(did,'platform_fee',d['platform_fee_cents'],'Platform revenue',now()))
        else:return RedirectResponse('/driver/app?notice=invalid_status#active',303)
        event(con,did,u['id'],status); notify(con,d['customer_id'],'Delivery update',f"Delivery #{did}: {status.replace('_',' ')}")
    return RedirectResponse('/driver/app#active',303)

@app.get('/driver/login',response_class=HTMLResponse)
def driver_login_page(request:Request,next:str='/driver/app',error:int=0,wrong:int=0):
    u=user_from_request(request)
    if u and u['role']=='driver':return RedirectResponse('/driver/app',303)
    return page(request,'driver_login.html',next_path='/driver/app',error=bool(error),logged_user=u)

@app.get('/driver/icon.svg')
def driver_icon():return Response('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="#b7ff3c"/><path d="M148 118h76v210h140v66H148z" fill="#07111f"/><circle cx="362" cy="150" r="46" fill="#6d5dfc"/></svg>''',media_type='image/svg+xml',headers={'Cache-Control':'public, max-age=86400'})
@app.get('/driver/icon-192.png')
def driver_icon_192():return Response(_icon_png(192),media_type='image/png',headers={'Cache-Control':'public, max-age=86400'})
@app.get('/driver/icon-512.png')
def driver_icon_512():return Response(_icon_png(512),media_type='image/png',headers={'Cache-Control':'public, max-age=86400'})
@app.get('/driver/manifest.webmanifest')
def driver_manifest():return JSONResponse({'id':'/driver/app','name':'LocalLoop Driver','short_name':'LocalLoop Driver','description':'Accept LocalLoop deliveries, navigate, track jobs and earnings.','start_url':'/driver/app','scope':'/driver/','display':'standalone','orientation':'portrait','background_color':'#07111f','theme_color':'#6d5dfc','categories':['business','navigation','productivity'],'icons':[{'src':'/driver/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any maskable'},{'src':'/driver/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'}]},media_type='application/manifest+json')
@app.get('/driver/sw.js')
def driver_service_worker():
    js="""const C='localloop-driver-v9';self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.map(k=>caches.delete(k)))).then(()=>self.clients.claim())));self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(ws=>ws.length?(ws[0].focus(),ws[0].navigate('/driver/app#offers')):clients.openWindow('/driver/app#offers')))});self.addEventListener('fetch',e=>{if(e.request.method!=='GET'||new URL(e.request.url).origin!==location.origin)return;e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))})"""
    return Response(js,media_type='application/javascript',headers={'Service-Worker-Allowed':'/driver/','Cache-Control':'no-store'})

@app.head('/')
def root_head():
    return Response(status_code=200)
