package com.localloop.driver

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.IBinder
import androidx.core.app.ActivityCompat
import com.google.android.gms.location.*
import kotlin.concurrent.thread

class LocationService : Service() {
    private lateinit var client: FusedLocationProviderClient
    private lateinit var callback: LocationCallback

    override fun onCreate() {
        super.onCreate()
        client = LocationServices.getFusedLocationProviderClient(this)
        val channelId = "localloop_location"
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(channelId, "Active delivery location", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val notification = Notification.Builder(this, channelId)
            .setContentTitle("LocalLoop Driver")
            .setContentText("Live GPS is active for your current job")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .build()
        startForeground(42, notification)

        callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                val loc = result.lastLocation ?: return
                val speed = if (loc.hasSpeed()) loc.speed.toDouble() else 0.0
                val bearing = if (loc.hasBearing()) loc.bearing.toDouble() else 0.0
                val accuracy = if (loc.hasAccuracy()) loc.accuracy.toDouble() else 0.0
                getSharedPreferences("localloop", MODE_PRIVATE).edit()
                    .putFloat("last_speed_mph", (speed * 2.236936).toFloat())
                    .putFloat("last_accuracy_m", accuracy.toFloat())
                    .apply()
                thread {
                    try { Api.location(this@LocationService, loc.latitude, loc.longitude, speed, bearing, accuracy) }
                    catch (_: Exception) { }
                }
            }
        }
        startUpdates()
    }

    private fun startUpdates() {
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            stopSelf(); return
        }
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 5000L)
            .setMinUpdateIntervalMillis(2500L)
            .setMinUpdateDistanceMeters(4f)
            .build()
        client.requestLocationUpdates(request, callback, mainLooper)
    }

    override fun onDestroy() {
        if (::callback.isInitialized) client.removeLocationUpdates(callback)
        getSharedPreferences("localloop", MODE_PRIVATE).edit().remove("last_speed_mph").remove("last_accuracy_m").apply()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
