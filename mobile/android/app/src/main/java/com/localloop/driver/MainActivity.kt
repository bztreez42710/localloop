package com.localloop.driver

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private lateinit var root: LinearLayout
    private lateinit var status: TextView

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

    private fun makeRoot(): LinearLayout {
        val scroll = ScrollView(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 44, 32, 72)
            setBackgroundColor(Color.rgb(7,17,31))
        }
        scroll.addView(root)
        setContentView(scroll)
        return root
    }

    private fun title(text: String) = TextView(this).apply {
        this.text = text; textSize = 28f; setTextColor(Color.WHITE); setPadding(0,12,0,18)
    }
    private fun label(text: String) = TextView(this).apply {
        this.text = text; textSize = 16f; setTextColor(Color.rgb(220,225,235)); setPadding(0,8,0,8)
    }
    private fun button(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text; setOnClickListener { onClick() }
    }
    private fun input(hint: String, password: Boolean=false) = EditText(this).apply {
        this.hint = hint; setTextColor(Color.WHITE); setHintTextColor(Color.GRAY); setSingleLine(true)
        if(password) inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
    }

    private fun showEntry() {
        makeRoot()
        root.addView(title("LocalLoop Driver"))
        val token = getSharedPreferences("localloop", MODE_PRIVATE).getString("token", "") ?: ""
        if (token.isBlank()) showLogin() else showDashboard()
    }

    private fun showLogin() {
        root.addView(label("Native driver app — sign in with your LocalLoop driver account."))
        val email = input("Email")
        val pass = input("Password", true)
        status = label("")
        root.addView(email); root.addView(pass); root.addView(button("Sign in") {
            status.text = "Signing in…"
            thread {
                try {
                    val result = Api.login(this, email.text.toString(), pass.text.toString())
                    Api.saveToken(this, result.getString("token"))
                    runOnUiThread { showEntry() }
                } catch (e: Exception) { runOnUiThread { status.text = e.message ?: "Login failed" } }
            }
        }); root.addView(status)
    }

    private fun showDashboard() {
        root.removeAllViews()
        root.addView(title("LocalLoop Driver"))
        status = label("Loading account…")
        root.addView(status)
        root.addView(button("Refresh") { refresh() })
        root.addView(button("Go online") { setOnline(true) })
        root.addView(button("Go offline") { setOnline(false) })
        root.addView(button("Start background GPS") { startGps() })
        root.addView(button("Stop background GPS") { stopGps() })
        root.addView(button("Earnings") { loadEarnings() })
        root.addView(button("Log out") { logout() })
        root.addView(label("Offers and active jobs"))
        refresh()
    }

    private fun refresh() {
        status.text = "Refreshing…"
        thread {
            try {
                val me = Api.me(this)
                val jobs = Api.offers(this)
                runOnUiThread {
                    status.text = "${me.optString("name")} · ${if(me.optBoolean("online")) "ONLINE" else "OFFLINE"} · balance $${"%.2f".format(me.optInt("payout_balance_cents")/100.0)}"
                    renderJobs(jobs)
                }
            } catch (e: Exception) { runOnUiThread { status.text = e.message ?: "Could not refresh" } }
        }
    }

    private fun renderJobs(data: JSONObject) {
        while (root.childCount > 9) root.removeViewAt(9)
        val active = data.optJSONArray("active") ?: JSONArray()
        val offers = data.optJSONArray("offers") ?: JSONArray()
        root.addView(label("Active (${active.length()})"))
        for (i in 0 until active.length()) addJobCard(active.getJSONObject(i), true)
        root.addView(label("Available offers (${offers.length()})"))
        for (i in 0 until offers.length()) addJobCard(offers.getJSONObject(i), false)
    }

    private fun navigateTo(address: String, label: String) {
        if (address.isBlank()) {
            status.text = "No address is available for $label"
            return
        }
        val navUri = Uri.parse("google.navigation:q=${Uri.encode(address)}&mode=d")
        val mapsIntent = Intent(Intent.ACTION_VIEW, navUri).apply { setPackage("com.google.android.apps.maps") }
        try {
            startActivity(mapsIntent)
        } catch (_: Exception) {
            val web = Uri.parse("https://www.google.com/maps/dir/?api=1&destination=${Uri.encode(address)}&travelmode=driving&dir_action=navigate")
            startActivity(Intent(Intent.ACTION_VIEW, web))
        }
    }

    private fun addJobCard(job: JSONObject, active: Boolean) {
        val id = job.getInt("id")
        val type = job.optString("job_type", "delivery")
        val isShopping = type == "shopping"
        val pickup = job.optString("pickup")
        val dropoff = job.optString("dropoff")
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(18,18,18,18); setBackgroundColor(Color.rgb(20,29,48))
        }
        if (isShopping) {
            box.addView(label("Shopping #$id · $${"%.2f".format(job.optInt("driver_pay_cents")/100.0)} shopper pay"))
            box.addView(label("Merchandise budget: $${"%.2f".format(job.optInt("estimated_goods_cents")/100.0)}"))
        } else {
            box.addView(label("Delivery #$id · $${"%.2f".format(job.optInt("driver_pay_cents")/100.0)} · ${job.optDouble("distance_miles")} mi"))
        }
        box.addView(label("$pickup → $dropoff"))
        box.addView(label(job.optString("item_description")))

        if (!active) {
            box.addView(button(if(isShopping) "Accept shopping job" else "Accept delivery") {
                doAction { if(isShopping) Api.acceptShopping(this,id) else Api.accept(this,id) }
            })
        } else if (isShopping) {
            when (job.optString("status")) {
                "accepted" -> {
                    box.addView(button("Navigate to store") { navigateTo(pickup,"store") })
                    box.addView(button("Start shopping") { doAction { Api.shoppingStatus(this,id,"shopping") } })
                }
                "shopping" -> {
                    box.addView(button("Navigate to store") { navigateTo(pickup,"store") })
                    val total = input("Actual merchandise total, e.g. 8.75")
                    box.addView(total)
                    box.addView(button("Finish shopping / start delivery") {
                        val cents = ((total.text.toString().toDoubleOrNull() ?: -1.0) * 100.0).toInt()
                        if (cents < 0) status.text = "Enter the receipt total first"
                        else doAction { Api.shoppingStatus(this,id,"delivering",cents) }
                    })
                }
                "delivering" -> {
                    box.addView(button("Navigate to customer") { navigateTo(dropoff,"customer") })
                    box.addView(button("Complete shopping delivery") { doAction { Api.shoppingStatus(this,id,"delivered") } })
                }
            }
        } else if (job.optString("status") == "accepted") {
            box.addView(button("Navigate to pickup") { navigateTo(pickup,"pickup") })
            box.addView(button("Mark picked up") { doAction { Api.status(this,id,"picked_up") } })
        } else {
            box.addView(button("Navigate to customer") { navigateTo(dropoff,"customer") })
            val proof = input("Delivery note / proof")
            val handoff = input("Customer handoff code if required")
            box.addView(proof); box.addView(handoff)
            box.addView(button("Complete delivery") { doAction { Api.status(this,id,"delivered",proof.text.toString(),handoff.text.toString()) } })
        }
        root.addView(box)
        root.addView(View(this).apply { minimumHeight = 16 })
    }

    private fun doAction(call: () -> JSONObject) {
        status.text = "Working…"
        thread { try { call(); runOnUiThread { refresh() } } catch(e:Exception) { runOnUiThread { status.text=e.message ?: "Action failed" } } }
    }

    private fun setOnline(on:Boolean) {
        doAction { Api.online(this,on) }
        if (!on) stopGps()
    }

    private fun startGps() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestNeededPermissions(); status.text="Allow location, then tap Start background GPS again"; return
        }
        startForegroundService(Intent(this, LocationService::class.java))
        status.text = "Background GPS started for active job"
    }

    private fun stopGps() {
        stopService(Intent(this, LocationService::class.java))
        status.text = "Background GPS stopped"
    }

    private fun loadEarnings() {
        status.text = "Loading earnings…"
        thread {
            try {
                val e=Api.earnings(this)
                val h=e.optJSONArray("history") ?: JSONArray()
                val b=StringBuilder("Balance: $${"%.2f".format(e.optInt("payout_balance_cents")/100.0)}\nCompleted: ${e.optInt("completed")}\n\n")
                for(i in 0 until minOf(h.length(),20)) {
                    val r=h.getJSONObject(i)
                    val name=if(r.isNull("delivery_id") || r.optInt("delivery_id") == 0) r.optString("note","Shopping earning") else "Delivery #${r.optInt("delivery_id")}"
                    b.append("$name: $${"%.2f".format(r.optInt("amount_cents")/100.0)}\n")
                }
                runOnUiThread { status.text=b.toString() }
            } catch(e:Exception) { runOnUiThread { status.text=e.message ?: "Could not load earnings" } }
        }
    }

    private fun logout() {
        stopGps()
        thread {
            try { Api.logout(this) } catch (_:Exception) { }
            Api.clearToken(this)
            runOnUiThread { showEntry() }
        }
    }
}
