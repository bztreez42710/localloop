package com.localloop.driver

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ActivityCompat
import com.google.android.gms.location.*
import kotlin.concurrent.thread

class LocationService : Service() {
    private lateinit var client: FusedLocationProviderClient
    private lateinit var callback: LocationCallback
    private val handler=Handler(Looper.getMainLooper())
    private var lastSmartCheck=0L
    private var arrivalNotifiedFor=""
    private val staleCheck=object:Runnable{override fun run(){
        val p=getSharedPreferences("localloop",MODE_PRIVATE); val last=p.getLong("last_gps_epoch",0L)
        if(last>0 && System.currentTimeMillis()-last>45000L) notifyDriver(45,"GPS signal needs attention","LocalLoop has not received a fresh GPS fix for over 45 seconds.")
        handler.postDelayed(this,30000L)
    }}

    override fun onCreate() {
        super.onCreate(); client=LocationServices.getFusedLocationProviderClient(this)
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            val nm=getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel("localloop_location","Active delivery location",NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel("localloop_alerts","Driver alerts",NotificationManager.IMPORTANCE_HIGH))
        }
        val open=PendingIntent.getActivity(this,0,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification=Notification.Builder(this,"localloop_location").setContentTitle("LocalLoop Driver").setContentText("Live job GPS is active").setSmallIcon(android.R.drawable.ic_menu_mylocation).setContentIntent(open).setOngoing(true).build()
        startForeground(42,notification)
        callback=object:LocationCallback(){override fun onLocationResult(result:LocationResult){
            val loc=result.lastLocation?:return
            val mph=if(loc.hasSpeed())loc.speed*2.236936f else 0f
            getSharedPreferences("localloop",MODE_PRIVATE).edit().putFloat("last_speed_mph",mph).putFloat("last_accuracy_m",if(loc.hasAccuracy())loc.accuracy else 0f).putLong("last_gps_epoch",System.currentTimeMillis()).putString("last_lat",loc.latitude.toString()).putString("last_lon",loc.longitude.toString()).apply()
            thread{
                try{Api.flushQueue(this@LocationService)}catch(_:Exception){}
                try{Api.location(this@LocationService,loc.latitude,loc.longitude,if(loc.hasSpeed())loc.speed.toDouble() else 0.0,if(loc.hasBearing())loc.bearing.toDouble() else 0.0,if(loc.hasAccuracy())loc.accuracy.toDouble() else 0.0)}catch(_:Exception){}
                val n=System.currentTimeMillis()
                if(n-lastSmartCheck>12000L){lastSmartCheck=n;try{
                    val d=Api.smartDashboard(this@LocationService); val j=d.optJSONObject("current_job")
                    if(j!=null){
                        val key="${j.optString("kind")}:${j.optInt("id")}:${j.optString("next_label")}";
                        if(j.optBoolean("arrived")&&arrivalNotifiedFor!=key){arrivalNotifiedFor=key;notifyDriver(46,"You’ve arrived","${j.optString("next_label")} is nearby. Open LocalLoop for the next step.")}
                        if(j.optBoolean("route_deviation"))notifyDriver(47,"Route check","You appear to be well off the expected route. Check navigation when safe.")
                    }
                }catch(_:Exception){}}
            }
        }}
        startUpdates();handler.postDelayed(staleCheck,30000L)
    }

    private fun notifyDriver(id:Int,title:String,text:String){
        val open=PendingIntent.getActivity(this,id,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n=Notification.Builder(this,"localloop_alerts").setContentTitle(title).setContentText(text).setSmallIcon(android.R.drawable.ic_dialog_map).setAutoCancel(true).setContentIntent(open).build()
        getSystemService(NotificationManager::class.java).notify(id,n)
    }
    private fun startUpdates(){
        if(ActivityCompat.checkSelfPermission(this,Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED){stopSelf();return}
        val request=LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY,5000L).setMinUpdateIntervalMillis(3000L).setMinUpdateDistanceMeters(5f).build()
        client.requestLocationUpdates(request,callback,mainLooper)
    }
    override fun onDestroy(){if(::callback.isInitialized)client.removeLocationUpdates(callback);handler.removeCallbacks(staleCheck);super.onDestroy()}
    override fun onBind(intent:Intent?):IBinder?=null
}
