from __future__ import annotations
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from .main import app, db, page, user_from_request

@app.get('/driver/app', response_class=HTMLResponse)
def driver_app(request: Request):
    u=user_from_request(request)
    if not u:
        return RedirectResponse('/driver/login?next='+quote('/driver/app',safe=''),303)
    if u['role']!='driver':
        return RedirectResponse('/driver/login?next='+quote('/driver/app',safe='')+'&wrong=1',303)
    with db() as con:
        profile=con.execute('SELECT * FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        available=con.execute("SELECT d.*,u.name customer FROM deliveries d JOIN users u ON u.id=d.customer_id WHERE d.status='posted' ORDER BY d.id DESC LIMIT 40").fetchall()
        mine=con.execute("SELECT * FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY d.id DESC".replace('d.id','id'),(u['id'],)).fetchall()
    return page(request,'driver_app.html',profile=profile,available=available,mine=mine)

@app.get('/driver/login', response_class=HTMLResponse)
def driver_login_page(request:Request,next:str='/driver/app',wrong:int=0):
    u=user_from_request(request)
    if u and u['role']=='driver': return RedirectResponse('/driver/app',303)
    return page(request,'driver_login.html',next_path='/driver/app',wrong=bool(wrong),logged_user=u)

@app.get('/driver/manifest.webmanifest')
def driver_manifest():
    return JSONResponse({'id':'/driver/app','name':'LocalLoop Driver','short_name':'LocalLoop Driver','description':'Accept LocalLoop deliveries, navigate, track jobs and earnings.','start_url':'/driver/app','scope':'/driver/','display':'standalone','orientation':'portrait','background_color':'#07111f','theme_color':'#6d5dfc','categories':['business','navigation','productivity'],'icons':[]},media_type='application/manifest+json')

@app.get('/driver/sw.js')
def driver_service_worker():
    js="""const C='localloop-driver-v2';self.addEventListener('install',e=>{self.skipWaiting()});self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;if(new URL(e.request.url).origin!==location.origin)return;e.respondWith(fetch(e.request).then(r=>{if(r.ok&&e.request.destination!=='document'){const x=r.clone();caches.open(C).then(c=>c.put(e.request,x))}return r}).catch(()=>caches.match(e.request)))})"""
    return Response(js,media_type='application/javascript',headers={'Service-Worker-Allowed':'/driver/','Cache-Control':'no-cache'})
