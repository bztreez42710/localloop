package com.localloop.driver

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private lateinit var root: LinearLayout
    private lateinit var status: TextView
    private lateinit var jobsContainer: LinearLayout
    private val bg = Color.rgb(7,17,31)
    private val card = Color.rgb(20,29,48)
    private val lime = Color.rgb(199,255,74)
    private val muted = Color.rgb(166,178,197)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNeededPermissions()
        showEntry()
    }

    private fun requestNeededPermissions() {
        val perms = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)
        if (android.os.Build.VERSION.SDK_INT >= 33) perms += Manifest.permission.POST_NOTIFICATIONS
        if (perms.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) requestPermissions(perms.toTypedArray(), 7)
    }

    private fun rounded(color: Int, radius: Float = 24f, stroke: Int? = null): GradientDrawable = GradientDrawable().apply {
        setColor(color); cornerRadius = radius
        if (stroke != null) setStroke(1, stroke)
    }

    private fun makeRoot(): LinearLayout {
        val scroll = ScrollView(this).apply { isFillViewport = true; setBackgroundColor(bg) }
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 42, 28, 72)
            setBackgroundColor(bg)
        }
        scroll.addView(root)
        setContentView(scroll)
        return root
    }

    private fun title(text: String) = TextView(this).apply {
        this.text = text; textSize = 30f; setTextColor(Color.WHITE); setTypeface(typeface, Typeface.BOLD); setPadding(0,10,0,4)
    }
    private fun eyebrow(text: String) = TextView(this).apply {
        this.text = text.uppercase(); textSize = 11f; letterSpacing = .12f; setTextColor(lime); setTypeface(typeface, Typeface.BOLD); setPadding(0,0,0,6)
    }
    private fun label(text: String, size: Float = 16f, color: Int = Color.rgb(220,225,235)) = TextView(this).apply {
        this.text = text; textSize = size; setTextColor(color); setPadding(0,6,0,6)
    }
    private fun cardBox(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(22,20,22,20)
        background = rounded(card, 22f, Color.rgb(42,56,78))
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0,10,0,10) }
    }
    private fun button(text: String, primary: Boolean = false, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 15f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(if (primary) Color.rgb(5,12,20) else Color.WHITE)
        background = rounded(if (primary) lime else Color.rgb(31,45,67), 18f, if(primary) null else Color.rgb(61,79,108))
        setPadding(18,12,18,12)
        minHeight = 54
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0,5,0,5) }
    }
    private fun input(hint: String, password: Boolean=false) = EditText(this).apply {
        this.hint = hint; setTextColor(Color.WHITE); setHintTextColor(Color.rgb(120,134,156)); setSingleLine(true)
        background = rounded(Color.rgb(10,20,35), 16f, Color.rgb(52,67,91)); setPadding(18,12,18,12)
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0,6,0,6) }
        if(password) inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
    }

    private fun showEntry() {
        makeRoot()
        root.addView(eyebrow("LocalLoop"))
        root.addView(title("Driver"))
        val token = getSharedPreferences("localloop", MODE_PRIVATE).getString("token", "") ?: ""
        if (token.isBlank()) showLogin() else showDashboard()
    }

    private fun showLogin() {
        root.addView(label("Sign in to accept local jobs, navigate, track earnings, and cash out.", 16f, muted))
        val box=cardBox(); val email=input("Email"); val pass=input("Password", true); status=label("")
        box.addView(email); box.addView(pass); box.addView(button("Sign in", true) {
            status.text = "Signing in…"
            thread {
                try {
                    val result = Api.login(this, email.text.toString(), pass.text.toString())
                    Api.saveToken(this, result.getString("token"))
                    runOnUiThread { showEntry() }
                } catch (e: Exception) { runOnUiThread { status.text = e.message ?: "Login failed" } }
            }
        }); box.addView(status); root.addView(box)
    }

    private fun showDashboard() {
        root.removeAllViews()
        root.addView(eyebrow("LocalLoop")); root.addView(title("Driver dashboard"))
        root.addView(label("One place for jobs, navigation, GPS, earnings, and payouts.", 15f, muted))

        val summary=cardBox(); status=label("Loading account…",17f); summary.addView(status)
        val row=LinearLayout(this).apply { orientation=LinearLayout.HORIZONTAL; gravity=Gravity.CENTER }
        val online=button("Go online", true) { setOnline(true) }; val offline=button("Go offline") { setOnline(false) }
        online.layoutParams=LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1f).apply{setMargins(0,4,5,4)}
        offline.layoutParams=LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1f).apply{setMargins(5,4,0,4)}
        row.addView(online); row.addView(offline); summary.addView(row)
        summary.addView(button("Refresh dashboard") { refresh() }); root.addView(summary)

        val gps=cardBox(); gps.addView(eyebrow("Live location")); gps.addView(label("GPS shares only while you are online with an active job. LocalLoop records route movement, GPS accuracy, and device-reported speed for active-job operations.",14f,muted))
        gps.addView(button("Start live GPS", true) { startGps() }); gps.addView(button("Stop live GPS") { stopGps() }); root.addView(gps)

        val money=cardBox(); money.addView(eyebrow("Money")); money.addView(button("Earnings") { loadEarnings() }); money.addView(button("Payouts & cash out", true) { loadPayouts() }); root.addView(money)

        root.addView(eyebrow("Jobs")); jobsContainer=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}; root.addView(jobsContainer)
        root.addView(button("Log out") { logout() })
        refresh()
    }

    private fun refresh() {
        status.text = "Refreshing…"
        thread {
            try {
                val me = Api.me(this); val jobs = Api.offers(this)
                runOnUiThread {
                    val prefs=getSharedPreferences("localloop",MODE_PRIVATE)
                    val mph=prefs.getFloat("last_speed_mph",-1f)
                    val gpsText=if(mph>=0f) " · GPS ${"%.0f".format(mph)} mph" else ""
                    status.text = "${me.optString("name")}\n${if(me.optBoolean("online")) "● ONLINE" else "○ OFFLINE"} · Available $${"%.2f".format(me.optInt("payout_balance_cents")/100.0)}$gpsText"
                    renderJobs(jobs)
                }
            } catch (e: Exception) { runOnUiThread { status.text = e.message ?: "Could not refresh" } }
        }
    }

    private fun renderJobs(data: JSONObject) {
        jobsContainer.removeAllViews()
        val active = data.optJSONArray("active") ?: JSONArray(); val offers = data.optJSONArray("offers") ?: JSONArray()
        jobsContainer.addView(label("Active jobs · ${active.length()}",20f,Color.WHITE))
        if(active.length()==0) jobsContainer.addView(label("No active jobs yet. Go online to see offers.",14f,muted))
        for (i in 0 until active.length()) addJobCard(active.getJSONObject(i), true)
        jobsContainer.addView(label("Available offers · ${offers.length()}",20f,Color.WHITE))
        if(offers.length()==0) jobsContainer.addView(label("No offers available right now.",14f,muted))
        for (i in 0 until offers.length()) addJobCard(offers.getJSONObject(i), false)
    }

    private fun navigateTo(address: String, label: String) {
        if (address.isBlank()) { status.text = "No address is available for $label"; return }
        val navUri = Uri.parse("google.navigation:q=${Uri.encode(address)}&mode=d")
        val mapsIntent = Intent(Intent.ACTION_VIEW, navUri).apply { setPackage("com.google.android.apps.maps") }
        try { startActivity(mapsIntent) }
        catch (_: Exception) {
            val web = Uri.parse("https://www.google.com/maps/dir/?api=1&destination=${Uri.encode(address)}&travelmode=driving&dir_action=navigate")
            startActivity(Intent(Intent.ACTION_VIEW, web))
        }
    }

    private fun addJobCard(job: JSONObject, active: Boolean) {
        val id=job.getInt("id"); val isShopping=job.optString("job_type","delivery")=="shopping"; val pickup=job.optString("pickup"); val dropoff=job.optString("dropoff")
        val box=cardBox()
        box.addView(eyebrow(if(isShopping) "Shopping #$id" else "Delivery #$id"))
        val pay=job.optInt("driver_pay_cents")/100.0
        box.addView(label("$${"%.2f".format(pay)} ${if(isShopping) "shopper pay" else "driver pay"}",22f,Color.WHITE))
        if(isShopping) box.addView(label("Merchandise budget $${"%.2f".format(job.optInt("estimated_goods_cents")/100.0)}",14f,muted))
        else box.addView(label("${job.optDouble("distance_miles")} miles",14f,muted))
        box.addView(label("Pickup\n$pickup",15f)); box.addView(label("Drop-off\n$dropoff",15f)); box.addView(label(job.optString("item_description"),14f,muted))

        if (!active) {
            box.addView(button(if(isShopping) "Accept shopping job" else "Accept delivery", true) {
                doAction(startGpsAfter=true) { if(isShopping) Api.acceptShopping(this,id) else Api.accept(this,id) }
            })
        } else if (isShopping) {
            when(job.optString("status")) {
                "accepted" -> { box.addView(button("Navigate to store",true){navigateTo(pickup,"store")}); box.addView(button("Start shopping"){doAction{Api.shoppingStatus(this,id,"shopping")}}) }
                "shopping" -> {
                    box.addView(button("Navigate to store",true){navigateTo(pickup,"store")}); val total=input("Receipt total, e.g. 8.75"); box.addView(total)
                    box.addView(button("Finish shopping / start delivery",true){ val cents=((total.text.toString().toDoubleOrNull()?:-1.0)*100).toInt(); if(cents<0) status.text="Enter the receipt total first" else doAction{Api.shoppingStatus(this,id,"delivering",cents)} })
                }
                "delivering" -> { box.addView(button("Navigate to customer",true){navigateTo(dropoff,"customer")}); box.addView(button("Complete shopping delivery"){doAction{Api.shoppingStatus(this,id,"delivered")}}) }
            }
        } else if(job.optString("status")=="accepted") {
            box.addView(button("Navigate to pickup",true){navigateTo(pickup,"pickup")}); box.addView(button("Mark picked up"){doAction{Api.status(this,id,"picked_up")}})
        } else {
            box.addView(button("Navigate to customer",true){navigateTo(dropoff,"customer")}); val proof=input("Delivery note / proof"); val handoff=input("Customer handoff code if required"); box.addView(proof); box.addView(handoff)
            box.addView(button("Complete delivery",true){doAction{Api.status(this,id,"delivered",proof.text.toString(),handoff.text.toString())}})
        }
        jobsContainer.addView(box)
    }

    private fun doAction(startGpsAfter:Boolean=false, call: () -> JSONObject) {
        status.text="Working…"
        thread { try { call(); runOnUiThread { if(startGpsAfter) startGps(); refresh() } } catch(e:Exception) { runOnUiThread { status.text=e.message ?: "Action failed" } } }
    }

    private fun setOnline(on:Boolean) { doAction { Api.online(this,on) }; if(!on) stopGps() }

    private fun startGps() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) { requestNeededPermissions(); status.text="Allow precise location, then tap Start live GPS again"; return }
        startForegroundService(Intent(this, LocationService::class.java)); status.text="Live GPS started for active job"
    }
    private fun stopGps() { stopService(Intent(this, LocationService::class.java)); status.text="Live GPS stopped" }

    private fun loadEarnings() {
        status.text="Loading earnings…"
        thread {
            try {
                val e=Api.earnings(this); val h=e.optJSONArray("history")?:JSONArray(); val b=StringBuilder("Available: $${"%.2f".format(e.optInt("payout_balance_cents")/100.0)}\nCompleted jobs: ${e.optInt("completed")}\n\nRecent earnings\n")
                for(i in 0 until minOf(h.length(),12)){ val r=h.getJSONObject(i); val name=if(r.isNull("delivery_id")||r.optInt("delivery_id")==0) r.optString("note","Shopping earning") else "Delivery #${r.optInt("delivery_id")}"; b.append("$name  +$${"%.2f".format(r.optInt("amount_cents")/100.0)}\n") }
                runOnUiThread { AlertDialog.Builder(this).setTitle("Earnings").setMessage(b.toString()).setPositiveButton("OK",null).show() }
            } catch(e:Exception){runOnUiThread{status.text=e.message?:"Could not load earnings"}}
        }
    }

    private fun loadPayouts() {
        status.text="Checking payout account…"
        thread {
            try {
                val p=Api.payout(this); runOnUiThread {
                    val available=p.optInt("available_cents")
                    if(!p.optBoolean("connected") || !p.optBoolean("details_submitted") || !p.optBoolean("transfers_active")) {
                        AlertDialog.Builder(this).setTitle("Set up driver payouts").setMessage("Connect a Stripe Express payout account so LocalLoop can send your available earnings to you securely.").setNegativeButton("Not now",null).setPositiveButton("Set up") { _,_-> startPayoutOnboarding() }.show()
                    } else {
                        AlertDialog.Builder(this).setTitle("Cash out").setMessage("Available earnings: $${"%.2f".format(available/100.0)}\n\nCash out sends these earnings to your Stripe connected account. Stripe then pays your linked bank account according to its payout timing.").setNegativeButton("Cancel",null).setPositiveButton(if(available>=100) "Cash out all" else "OK") { _,_-> if(available>=100) cashOutAll() }.show()
                    }
                }
            } catch(e:Exception){runOnUiThread{status.text=e.message?:"Could not load payout status"}}
        }
    }

    private fun startPayoutOnboarding() {
        status.text="Opening secure Stripe setup…"
        thread { try { val r=Api.payoutOnboard(this); val url=r.optString("url"); runOnUiThread { if(url.isBlank()) status.text="Stripe setup link was not returned" else startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(url))) } } catch(e:Exception){runOnUiThread{status.text=e.message?:"Could not start payout setup"}} }
    }

    private fun cashOutAll() {
        status.text="Sending payout…"
        thread { try { val r=Api.payoutRequest(this); runOnUiThread { AlertDialog.Builder(this).setTitle("Payout sent").setMessage("$${"%.2f".format(r.optInt("amount_cents")/100.0)} was sent to your Stripe payout account.").setPositiveButton("OK",null).show(); refresh() } } catch(e:Exception){runOnUiThread{status.text=e.message?:"Cash out failed"}} }
    }

    private fun logout() {
        stopGps(); thread { try { Api.logout(this) } catch (_:Exception) { }; Api.clearToken(this); runOnUiThread { showEntry() } }
    }
}
