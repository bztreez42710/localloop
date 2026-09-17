package com.localloop.driver

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

object Api {
    const val BASE = "https://localloop-app.onrender.com"

    private fun token(ctx: Context): String = ctx.getSharedPreferences("localloop", Context.MODE_PRIVATE).getString("token", "") ?: ""
    fun saveToken(ctx: Context, token: String) = ctx.getSharedPreferences("localloop", Context.MODE_PRIVATE).edit().putString("token", token).apply()
    fun clearToken(ctx: Context) = saveToken(ctx, "")

    private fun request(ctx: Context, method: String, path: String, form: Map<String,String> = emptyMap(), auth: Boolean = true): JSONObject {
        val conn = URL(BASE + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        conn.setRequestProperty("Accept", "application/json")
        if (auth) conn.setRequestProperty("Authorization", "Bearer ${token(ctx)}")
        if (method == "POST") {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
            val body = form.entries.joinToString("&") { "${URLEncoder.encode(it.key,"UTF-8")}=${URLEncoder.encode(it.value,"UTF-8")}" }
            conn.outputStream.use { it.write(body.toByteArray()) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use(BufferedReader::readText) ?: "{}"
        if (code !in 200..299) {
            val detail = try { JSONObject(text).optString("detail", "Request failed") } catch (_: Exception) { "Request failed" }
            throw RuntimeException("$detail ($code)")
        }
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }

    fun login(ctx: Context, email: String, password: String): JSONObject = request(ctx,"POST","/api/mobile/login", mapOf("email" to email,"password" to password,"platform" to "android"), false)
    fun me(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/me")
    fun offers(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/offers")
    fun earnings(ctx: Context): JSONObject = request(ctx,"GET","/api/mobile/earnings")
    fun online(ctx: Context, on: Boolean): JSONObject = request(ctx,"POST","/api/mobile/online", mapOf("online" to if(on) "1" else "0"))
    fun accept(ctx: Context, id: Int): JSONObject = request(ctx,"POST","/api/mobile/deliveries/$id/accept")
    fun status(ctx: Context, id: Int, status: String, proof: String = "", handoff: String = ""): JSONObject = request(ctx,"POST","/api/mobile/deliveries/$id/status", mapOf("status" to status,"proof" to proof,"handoff_code" to handoff))
    fun acceptShopping(ctx: Context, id: Int): JSONObject = request(ctx,"POST","/api/mobile/shopping/$id/accept")
    fun shoppingStatus(ctx: Context, id: Int, status: String, actualGoodsCents: Int = 0): JSONObject = request(ctx,"POST","/api/mobile/shopping/$id/status", mapOf("status" to status,"actual_goods_cents" to actualGoodsCents.toString()))
    fun location(ctx: Context, lat: Double, lon: Double): JSONObject = request(ctx,"POST","/api/mobile/location", mapOf("latitude" to lat.toString(),"longitude" to lon.toString()))
    fun logout(ctx: Context): JSONObject = request(ctx,"POST","/api/mobile/logout")
}
