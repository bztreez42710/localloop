from __future__ import annotations
import os
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from .main import app, require_user, page
from .payments import configured as payments_configured, FINIX_ENV
from .storage import configured as storage_configured

RENDER_SERVICE_DASHBOARD='https://dashboard.render.com/web/srv-dak31mh5efls73fsc6t0'


def _checks():
    database_url=os.environ.get('DATABASE_URL','').strip()
    return {
        'database':bool(database_url.startswith('postgres://') or database_url.startswith('postgresql://')),
        'finix':payments_configured(),
        'finix_live':payments_configured() and FINIX_ENV in {'live','prod','production'},
        'email':bool(os.environ.get('RESEND_API_KEY','').strip() and os.environ.get('RESEND_FROM_EMAIL','').strip()),
        'storage':storage_configured(),
        'identity':bool(os.environ.get('PERSONA_API_KEY','').strip() and os.environ.get('PERSONA_TEMPLATE_ID','').strip()),
        'background':bool(os.environ.get('CHECKR_API_KEY','').strip() and os.environ.get('CHECKR_PACKAGE','').strip()),
        'secure_cookie':os.environ.get('LOCALLOOP_COOKIE_SECURE','0')=='1',
    }


def _steps(checks):
    return [
        {
            'key':'database','title':'1. Connect free external PostgreSQL','ready':checks['database'],
            'why':'Removes LocalLoop from the expiring Render free database and keeps the app portable. Supabase is the preferred free option for this stage.',
            'provider_url':'https://supabase.com/dashboard/projects','provider_label':'Open Supabase',
            'instructions':[
                'Create a free Supabase account/project named LocalLoop.',
                'Open Project Settings → Database and copy a PostgreSQL connection string. Use the session/pooler connection string if direct IPv6 connectivity is unavailable from Render.',
                'Replace the password placeholder with the database password you chose when creating the Supabase project.',
                'Open the LocalLoop Render web service Environment page and set DATABASE_URL to that full PostgreSQL URL.',
                'Save changes and let Render redeploy LocalLoop. The app already supports standard PostgreSQL URLs, so no provider-specific database code is required.',
                'Keep the old Render database only as temporary fallback until LocalLoop starts successfully on Supabase, then stop relying on it.'
            ],
            'env':['DATABASE_URL=<Supabase PostgreSQL connection string>'],
            'secret_note':'Treat DATABASE_URL as a password. Never post it in chat or commit it to GitHub. Supabase Free is $0/month but currently pauses projects after one week of inactivity; LocalLoop traffic will normally keep an active project awake. Provider terms can change, so LocalLoop remains portable instead of being locked to one database vendor.'
        },
        {
            'key':'finix_live','title':'2. Turn on Finix Live','ready':checks['finix_live'],
            'why':'Enables real LocalLoop Pay charges and processor-backed refunds.',
            'provider_url':'https://dashboard.finix.com/','provider_label':'Open Finix Dashboard',
            'instructions':[
                'Complete Finix production onboarding/underwriting for LocalLoop.',
                'Switch to the Live environment in Finix.',
                'Go to Developers → API Keys and create a Live API key.',
                'Copy the Live username/password plus your Live application and merchant IDs.',
                'Add the variables below to the LocalLoop Render service.'
            ],
            'env':['FINIX_ENV=live','FINIX_USERNAME=<live username>','FINIX_PASSWORD=<live password>','FINIX_APPLICATION_ID=<live application id>','FINIX_MERCHANT_ID=<live merchant id>'],
            'secret_note':'Keep FINIX_PASSWORD private. Live and Sandbox credentials are different.'
        },
        {
            'key':'background','title':'3. Credential Checkr','ready':checks['background'],
            'why':'Allows hosted driver background screening without LocalLoop collecting SSNs itself.',
            'provider_url':'https://dashboard.checkr.com/','provider_label':'Open Checkr Dashboard',
            'instructions':[
                'Create/finish the LocalLoop business account and Checkr credentialing.',
                'Ask Checkr to enable production API access after their review.',
                'In Account Settings → Developer Settings, create/copy the production Secret API key.',
                'Choose the Checkr package slug you want LocalLoop to use for driver invitations.',
                'Add the variables below to Render.'
            ],
            'env':['CHECKR_API_KEY=<production secret key>','CHECKR_PACKAGE=<package slug>'],
            'secret_note':'The Checkr secret key can create screening requests. Keep it server-side only.'
        },
        {
            'key':'identity','title':'4. Credential Persona','ready':checks['identity'],
            'why':'Lets drivers complete identity verification in Persona instead of uploading identity documents to LocalLoop.',
            'provider_url':'https://app.withpersona.com/','provider_label':'Open Persona Dashboard',
            'instructions':[
                'Finish Persona organization verification and select the plan needed for production use.',
                'Create an Inquiry Template for driver identity verification.',
                'Copy the template ID.',
                'Go to API → API Keys and create/copy the production API key.',
                'Add both variables below to Render.'
            ],
            'env':['PERSONA_API_KEY=<production API key>','PERSONA_TEMPLATE_ID=<inquiry template id>'],
            'secret_note':'Keep the Persona API key private. The template ID is not a password, but should still live in configuration.'
        },
        {
            'key':'email','title':'5. Verify email and connect Resend','ready':checks['email'],
            'why':'Makes password-reset and account emails actually deliver to users.',
            'provider_url':'https://resend.com/domains','provider_label':'Open Resend Domains',
            'instructions':[
                'Use a domain you own. A sending subdomain such as mail.yourdomain.com is a good choice.',
                'Add the domain in Resend and copy the SPF/DKIM DNS records into your domain DNS provider.',
                'Wait until Resend shows the domain as verified.',
                'Create a Sending-access API key in Resend.',
                'Choose a From address that exactly matches the verified domain and add both variables to Render.'
            ],
            'env':['RESEND_API_KEY=<Resend API key>','RESEND_FROM_EMAIL=LocalLoop <no-reply@your-verified-domain.com>'],
            'secret_note':'The API key is secret and is only shown once when created.'
        },
        {
            'key':'storage','title':'6. Connect private S3-compatible storage','ready':checks['storage'],
            'why':'Moves proof photos and receipts out of the application database into private object storage.',
            'provider_url':'https://dash.cloudflare.com/?to=/:account/r2','provider_label':'Open Cloudflare R2',
            'instructions':[
                'Enable R2 and create a private bucket, for example localloop-private.',
                'Create an R2 API token with Object Read & Write permission limited to that bucket.',
                'Copy the Access Key ID, Secret Access Key, and S3 endpoint URL.',
                'Add the variables below to Render.',
                'After confirming uploads work, set OBJECT_STORAGE_REQUIRED=1 so LocalLoop refuses insecure database image fallback.'
            ],
            'env':['OBJECT_STORAGE_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com','OBJECT_STORAGE_BUCKET=<bucket name>','OBJECT_STORAGE_ACCESS_KEY=<access key id>','OBJECT_STORAGE_SECRET_KEY=<secret access key>','OBJECT_STORAGE_REGION=auto','OBJECT_STORAGE_REQUIRED=1'],
            'secret_note':'Keep the secret access key private. The bucket should remain private; LocalLoop serves authorized files through its own routes.'
        },
    ]

@app.get('/admin/infrastructure',response_class=HTMLResponse)
def infrastructure_status(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    checks=_checks()
    ready_count=sum(1 for key in ['database','finix_live','email','storage','identity','background','secure_cookie'] if checks[key])
    return page(request,'infrastructure.html',checks=checks,steps=_steps(checks),ready_count=ready_count,total_count=7,finix_env=FINIX_ENV,render_service_url=RENDER_SERVICE_DASHBOARD)
