package com.localloop.driver

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.time.Instant

object Api {
    const val BASE = "https://localloop-app.onrender.com"
    class HttpError(val code:Int, message:String): RuntimeException(message)
    private fun prefs(ctx:Context)=ctx.getSharedPreferences("localloop",Context.MODE_PRIVATE)
    private fun token(ctx: Context): String = prefs(ctx).getString("token", "") ?: ""
    fun saveToken(ctx: Context, token: String) = prefs(ctx).edit().putString("token", token).apply()
    fun clearToken(ctx: Context) = saveToken(ctx, "")

    private fun request(ctx: Context, method: String, path: String, form: Map<String,String> = emptyMap(), auth: Boolean = true): JSONObject {
        val conn = URL(BASE + path).openConnection() as HttpURLConnection
        conn.requestMethod = method; conn.connectTimeout = 15000; conn.readTimeout = 20000
        conn.setRequestProperty("Accept", "application/json")
        if (auth) conn.setRequestProperty("Authorization", "Bearer ${token(ctx)}")
        if (method == "POST") {
            conn.doOutput = true; conn.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
            val body = form.entries.joinToString("&") { "${URLEncoder.encode(it.key,"UTF-8")}=${URLEncoder.encode(it.value,"UTF-8")}" }
            conn.outputStream.use { it.write(body.toByteArray()) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use(BufferedReader::readText) ?: "{}"
        if (code !in 200..299) {
            val detail = try { JSONObject(text).optString("detail", "Request failed") } catch (_: Exception) { "Request failed" }
            throw HttpError(code,"$detail ($code)")
        }
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }

    private fun queue(ctx:Context,path:String,form:Map<String,String>){
        val a=try{JSONArray(prefs(ctx).getString("offline_queue","[]"))}catch(_:Exception){JSONArray()}
        val o=JSONObject().put("path",path).put("form",JSONObject(form)).put("queued_at",Instant.now().toString())
        a.put(o); while(a.length()>120)a.remove(0); prefs(ctx).edit().putString("offline_queue",a.toString()).apply()
    }
    fun flushQueue(ctx:Context):Int{
        val a=try{JSONArray(prefs(ctx).getString("offline_queue","[]"))}catch(_:Exception){JSONArray()}
        if(a.length()==0)return 0
        val remain=JSONArray(); var sent=0
        for(i in 0 until a.length()){
            val o=a.getJSONObject(i); val f=o.optJSONObject("form")?:JSONObject(); val m=mutableMapOf<String,String>()
            f.keys().forEach{m[it]=f.optString(it)}
            try{request(ctx,"POST",o.getString("path"),m);sent++}
            catch(e:IOException){remain.put(o);for(j in i+1 until a.length())remain.put(a.getJSONObject(j));break}
            catch(e:HttpError){if(e.code>=500)remain.put(o)}
        }
        prefs(ctx).edit().putString("offline_queue",remain.toString()).apply(); return sent
    }
    private fun postQueueable(ctx:Context,path:String,form:Map<String,String>):JSONObject{
        return try{request(ctx,"POST",path,form)}catch(e:IOException){queue(ctx,path,form);JSONObject().put("queued",true)}
    }

    fun login(ctx: Context, email: String, password: String): JSONObject = request(ctx,"POST","/api/mobile/login", mapOf("email" to email,"password" to password,"platform" to "android"), false)
    fun me(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/me")
    fun offers(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/offers")
    fun smartDashboard(ctx:Context):JSONObject{try{flushQueue(ctx)}catch(_:Exception){};return request(ctx,"GET","/api/mobile/smart-dashboard")}
    fun earnings(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/earnings")
    fun money(ctx:Context):JSONObject=request(ctx,"GET","/api/mobile/money")
    fun payout(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/payout")
    fun payoutOnboard(ctx: Context): JSONObject = request(ctx,"POST","/api/mobile/payout/onboard")
    fun payoutRequest(ctx: Context, amountCents: Int = 0): JSONObject = request(ctx,"POST","/api/mobile/payout/request", mapOf("amount_cents" to amountCents.toString()))
    fun online(ctx: Context, on: Boolean): JSONObject = request(ctx,"POST","/api/mobile/online", mapOf("online" to if(on) "1" else "0"))
    fun accept(ctx: Context, id: Int): JSONObject = request(ctx,"POST","/api/mobile/deliveries/$id/accept")
    fun status(ctx: Context, id: Int, status: String, proof: String = "", handoff: String = ""): JSONObject = postQueueable(ctx,"/api/mobile/deliveries/$id/status", mapOf("status" to status,"proof" to proof,"handoff_code" to handoff))
    fun acceptShopping(ctx: Context, id: Int): JSONObject = request(ctx,"POST","/api/mobile/shopping/$id/accept")
    fun shoppingStatus(ctx: Context, id: Int, status: String, actualGoodsCents: Int = 0): JSONObject = postQueueable(ctx,"/api/mobile/shopping/$id/status", mapOf("status" to status,"actual_goods_cents" to actualGoodsCents.toString()))
    fun nextShoppingStop(ctx:Context,id:Int):JSONObject=request(ctx,"POST","/api/mobile/shopping/$id/next-stop")
    fun location(ctx: Context, lat: Double, lon: Double, speedMps: Double = 0.0, bearing: Double = 0.0, accuracyM: Double = 0.0): JSONObject {
        val form=mapOf("latitude" to lat.toString(),"longitude" to lon.toString(),"speed_mps" to speedMps.toString(),"bearing" to bearing.toString(),"accuracy_m" to accuracyM.toString(),"recorded_at" to Instant.now().toString())
        return postQueueable(ctx,"/api/mobile/location",form)
    }
    fun incident(ctx:Context,kind:String,details:String,lat:Double=0.0,lon:Double=0.0):JSONObject=request(ctx,"POST","/api/mobile/incidents",mapOf("kind" to kind,"details" to details,"latitude" to lat.toString(),"longitude" to lon.toString()))
    fun expense(ctx:Context,cents:Int,category:String,note:String):JSONObject=request(ctx,"POST","/api/mobile/expenses",mapOf("amount_cents" to cents.toString(),"category" to category,"note" to note))
    fun message(ctx:Context,kind:String,body:String=""):JSONObject=request(ctx,"POST","/api/mobile/messages",mapOf("kind" to kind,"body" to body))
    fun substitution(ctx:Context,original:String,proposed:String,photo:String=""):JSONObject=request(ctx,"POST","/api/mobile/substitutions",mapOf("original_item" to original,"proposed_item" to proposed,"photo_data" to photo))
    fun proof(ctx:Context,note:String="",deliveryPhoto:String="",receiptPhoto:String="",signature:String="",pin:String=""):JSONObject=request(ctx,"POST","/api/mobile/proof",mapOf("note" to note,"delivery_photo" to deliveryPhoto,"receipt_photo" to receiptPhoto,"signature" to signature,"customer_pin" to pin))
    fun routePlan(ctx:Context,id:Int,optimized:Boolean):JSONObject=request(ctx,"POST","/api/mobile/shopping/$id/route-plan",mapOf("mode" to if(optimized)"optimized" else "original"))
    fun logout(ctx: Context): JSONObject = request(ctx,"POST","/api/mobile/logout")
}
