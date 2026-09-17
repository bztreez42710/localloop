package com.localloop.driver

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.util.Base64
import android.view.Gravity
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private lateinit var root: LinearLayout
    private lateinit var status: TextView
    private lateinit var cockpit: LinearLayout
    private lateinit var offersBox: LinearLayout

    private val bg = Color.rgb(7, 17, 31)
    private val card = Color.rgb(20, 29, 48)
    private val lime = Color.rgb(199, 255, 74)
    private val muted = Color.rgb(166, 178, 197)
    private val red = Color.rgb(210, 70, 70)

    private var proofPhoto = ""
    private var receiptPhoto = ""
    private var substitutionPhoto = ""
    private var captureMode = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNeededPermissions()
        showEntry()
    }

    private fun requestNeededPermissions() {
        val permissions = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)
        if (android.os.Build.VERSION.SDK_INT >= 33) permissions += Manifest.permission.POST_NOTIFICATIONS
        if (permissions.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) {
            requestPermissions(permissions.toTypedArray(), 7)
        }
    }

    private fun rounded(color: Int, radius: Float = 22f, stroke: Int? = null) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius
        if (stroke != null) setStroke(1, stroke)
    }

    private fun label(textValue: String, size: Float = 16f, color: Int = Color.rgb(225, 230, 238)) = TextView(this).apply {
        text = textValue
        textSize = size
        setTextColor(color)
        setPadding(0, 6, 0, 6)
    }

    private fun eyebrow(textValue: String) = label(textValue.uppercase(), 11f, lime).apply {
        letterSpacing = .12f
        setTypeface(typeface, Typeface.BOLD)
    }

    private fun title(textValue: String) = label(textValue, 30f, Color.WHITE).apply {
        setTypeface(typeface, Typeface.BOLD)
    }

    private fun cardBox() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(22, 20, 22, 20)
        background = rounded(card, 22f, Color.rgb(44, 58, 80))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { setMargins(0, 8, 0, 8) }
    }

    private fun button(textValue: String, primary: Boolean = false, danger: Boolean = false, click: () -> Unit) = Button(this).apply {
        text = textValue
        isAllCaps = false
        textSize = 15f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(if (primary) Color.rgb(5, 12, 20) else Color.WHITE)
        background = rounded(
            when {
                danger -> red
                primary -> lime
                else -> Color.rgb(31, 45, 67)
            },
            18f,
            if (primary || danger) null else Color.rgb(61, 79, 108)
        )
        minHeight = 54
        setOnClickListener { click() }
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { setMargins(0, 5, 0, 5) }
    }

    private fun input(hintValue: String, password: Boolean = false) = EditText(this).apply {
        hint = hintValue
        setTextColor(Color.WHITE)
        setHintTextColor(Color.rgb(120, 134, 156))
        background = rounded(Color.rgb(10, 20, 35), 16f, Color.rgb(52, 67, 91))
        setPadding(18, 12, 18, 12)
        if (password) inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
    }

    private fun makeRoot() {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(bg)
        }
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 42, 28, 76)
            setBackgroundColor(bg)
        }
        scroll.addView(root)
        setContentView(scroll)
    }

    private fun showEntry() {
        makeRoot()
        root.addView(eyebrow("LocalLoop"))
        root.addView(title("Driver"))
        val token = getSharedPreferences("localloop", MODE_PRIVATE).getString("token", "") ?: ""
        if (token.isBlank()) showLogin() else showDashboard()
    }

    private fun showLogin() {
        root.addView(label("Sign in to work, navigate, track mileage, message customers, and cash out.", 15f, muted))
        val box = cardBox()
        val email = input("Email")
        val password = input("Password", true)
        status = label("")
        box.addView(email)
        box.addView(password)
        box.addView(button("Sign in", primary = true) {
            status.text = "Signing in…"
            thread {
                try {
                    val result = Api.login(this, email.text.toString(), password.text.toString())
                    Api.saveToken(this, result.getString("token"))
                    runOnUiThread { showEntry() }
                } catch (e: Exception) {
                    runOnUiThread { status.text = e.message ?: "Login failed" }
                }
            }
        })
        box.addView(status)
        root.addView(box)
    }

    private fun showDashboard() {
        root.removeAllViews()
        root.addView(eyebrow("LocalLoop"))
        root.addView(title("Driver cockpit"))
        root.addView(label("Your current job stays front and center. Navigation, GPS, safety, money, and offers stay one tap away.", 14f, muted))

        val summary = cardBox()
        status = label("Loading…", 17f)
        summary.addView(status)
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        val onlineButton = button("Go online", primary = true) { setOnline(true) }
        val offlineButton = button("Go offline") { setOnline(false) }
        onlineButton.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(0, 4, 5, 4) }
        offlineButton.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(5, 4, 0, 4) }
        row.addView(onlineButton)
        row.addView(offlineButton)
        summary.addView(row)
        summary.addView(button("Money, expenses & payouts") { openMoney() })
        summary.addView(button("Refresh") { refresh() })
        root.addView(summary)

        cockpit = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(cockpit)
        root.addView(eyebrow("Available offers"))
        offersBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(offersBox)
        root.addView(button("Log out") { logout() })
        refresh()
    }

    private fun refresh() {
        status.text = "Refreshing…"
        thread {
            try {
                val dashboard = Api.smartDashboard(this)
                runOnUiThread { renderDashboard(dashboard) }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Could not refresh" }
            }
        }
    }

    private fun renderDashboard(dashboard: JSONObject) {
        val driver = dashboard.optJSONObject("driver") ?: JSONObject()
        val money = dashboard.optJSONObject("money") ?: JSONObject()
        val online = driver.optBoolean("online")
        val prefs = getSharedPreferences("localloop", MODE_PRIVATE)
        val lastGps = prefs.getLong("last_gps_epoch", 0L)
        val gpsAge = System.currentTimeMillis() - lastGps
        val gpsState = when {
            lastGps == 0L -> "GPS waiting"
            gpsAge < 15_000 -> "GPS live"
            else -> "GPS stale"
        }
        status.text = "${driver.optString("name")}\n${if (online) "● ONLINE" else "○ OFFLINE"} · $gpsState · Available $${"%.2f".format(money.optInt("available_cents") / 100.0)}"
        val currentJob = dashboard.optJSONObject("current_job")
        renderCockpit(currentJob)
        renderOffers(dashboard.optJSONArray("offers") ?: JSONArray())
        if (online && currentJob != null) startGps()
    }

    private fun renderCockpit(job: JSONObject?) {
        cockpit.removeAllViews()
        if (job == null) {
            val box = cardBox()
            box.addView(eyebrow("Current job"))
            box.addView(label("No active job. Go online and choose an offer below.", 16f, muted))
            cockpit.addView(box)
            return
        }

        val box = cardBox()
        val kind = job.optString("kind")
        val id = job.optInt("id")
        val isShopping = kind == "shopping"
        box.addView(eyebrow("Current ${if (isShopping) "shopping" else "delivery"} #$id"))
        box.addView(label(job.optString("stage"), 24f, Color.WHITE))
        box.addView(label(job.optString("next_label"), 15f, lime))
        box.addView(label(job.optString("next_address"), 15f))

        val eta = job.optInt("eta_minutes", -1)
        if (eta > 0) {
            box.addView(label("About $eta min · ${job.optDouble("distance_to_next_miles", 0.0)} mi to next stop", 15f, muted))
        }
        box.addView(label("Expected earnings: $${"%.2f".format(job.optInt("expected_earnings_cents") / 100.0)} · Trip ${job.optDouble("trip_miles", 0.0)} mi", 15f, muted))
        if (isShopping && job.optInt("stop_count", 1) > 1) {
            box.addView(label("Store ${job.optInt("current_stop_index", 0) + 1} of ${job.optInt("stop_count")}", 14f, muted))
        }
        if (job.optString("notes").isNotBlank()) box.addView(label("Customer notes: ${job.optString("notes")}", 14f, muted))
        if (job.optBoolean("arrived")) box.addView(label("✓ You’ve arrived nearby", 17f, lime))
        if (job.optBoolean("gps_stale")) box.addView(label("⚠ GPS signal is stale. Check location permission and signal.", 15f, Color.rgb(255, 200, 90)))
        if (job.optBoolean("route_deviation")) box.addView(label("⚠ You appear well off the expected route.", 15f, Color.rgb(255, 200, 90)))

        box.addView(button("Navigate to ${job.optString("next_label")}", primary = true) { navigateTo(job.optString("next_address")) })
        addMessageButtons(box)
        box.addView(button("SOS / report safety incident", danger = true) { openIncident() })
        if (isShopping) box.addView(button("Route order: optimized / original") { chooseRoute(id) })
        box.addView(button("Take delivery proof photo") { captureMode = "proof"; openCamera() })
        if (isShopping) box.addView(button("Take receipt photo") { captureMode = "receipt"; openCamera() })

        val jobStatus = job.optString("status")
        when {
            !isShopping && jobStatus == "accepted" -> {
                box.addView(button("Mark picked up", primary = true) { runAction { Api.status(this, id, "picked_up") } })
            }
            !isShopping && jobStatus == "picked_up" -> {
                box.addView(button("Complete delivery", primary = true) { completeDelivery(id, false) })
            }
            isShopping && jobStatus == "accepted" -> {
                box.addView(button("Start shopping", primary = true) { runAction { Api.shoppingStatus(this, id, "shopping") } })
            }
            isShopping && jobStatus == "shopping" -> {
                box.addView(button("Offer substitution") { openSubstitution() })
                if (job.optBoolean("has_more_stops")) {
                    box.addView(button("Store complete — navigate to next store", primary = true) { runAction { Api.nextShoppingStop(this, id) } })
                } else {
                    box.addView(button("Finish shopping / start delivery", primary = true) { openReceiptTotal(id) })
                }
            }
            isShopping && jobStatus == "delivering" -> {
                box.addView(button("Complete shopping delivery", primary = true) { completeDelivery(id, true) })
            }
        }
        cockpit.addView(box)
    }

    private fun addMessageButtons(box: LinearLayout) {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        listOf("At store" to "at_store", "Unavailable" to "unavailable", "Outside" to "outside").forEach { (textValue, kind) ->
            val item = button(textValue) { sendPreset(kind) }
            item.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(2, 4, 2, 4) }
            row.addView(item)
        }
        box.addView(row)
        box.addView(button("Message customer") { openCustomMessage() })
    }

    private fun sendPreset(kind: String) = runAction { Api.message(this, kind) }

    private fun openCustomMessage() {
        val message = input("Message")
        AlertDialog.Builder(this)
            .setTitle("Message customer")
            .setView(message)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Send") { _, _ -> runAction { Api.message(this, "message", message.text.toString()) } }
            .show()
    }

    private fun openIncident() {
        val details = input("What happened? (optional)")
        AlertDialog.Builder(this)
            .setTitle("Safety / incident")
            .setMessage("This records a timestamped safety incident with your current active-job location for LocalLoop review.")
            .setView(details)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Send SOS") { _, _ ->
                val prefs = getSharedPreferences("localloop", MODE_PRIVATE)
                val lat = prefs.getString("last_lat", "0")?.toDoubleOrNull() ?: 0.0
                val lon = prefs.getString("last_lon", "0")?.toDoubleOrNull() ?: 0.0
                runAction { Api.incident(this, "sos", details.text.toString(), lat, lon) }
            }.show()
    }

    private fun chooseRoute(id: Int) {
        AlertDialog.Builder(this)
            .setTitle("Shopping route order")
            .setItems(arrayOf("Use LocalLoop optimized order", "Use customer’s original store order")) { _, which ->
                runAction { Api.routePlan(this, id, which == 0) }
            }.show()
    }

    private fun renderOffers(offers: JSONArray) {
        offersBox.removeAllViews()
        if (offers.length() == 0) {
            offersBox.addView(label("No offers right now.", 14f, muted))
            return
        }
        for (i in 0 until offers.length()) {
            val offer = offers.getJSONObject(i)
            val box = cardBox()
            val shopping = offer.optString("job_type") == "shopping"
            val id = offer.optInt("id")
            box.addView(eyebrow(if (shopping) "Shopping #$id" else "Delivery #$id"))
            box.addView(label("$${"%.2f".format(offer.optInt("driver_pay_cents") / 100.0)}", 24f, Color.WHITE))
            val payPerMile = if (offer.isNull("pay_per_mile")) "" else " · $${"%.2f".format(offer.optDouble("pay_per_mile"))}/mi"
            box.addView(label("${offer.optInt("stop_count", 1)} stop(s) · ~${offer.optInt("estimated_minutes", 0)} min$payPerMile", 14f, muted))
            box.addView(label(offer.optString("complexity"), 14f, muted))
            box.addView(label("${offer.optString("pickup")} → ${offer.optString("dropoff")}", 14f))
            box.addView(button(if (shopping) "Accept shopping job" else "Accept delivery", primary = true) {
                runAction(startGpsAfter = true) {
                    if (shopping) Api.acceptShopping(this, id) else Api.accept(this, id)
                }
            })
            offersBox.addView(box)
        }
    }

    private fun navigateTo(address: String) {
        if (address.isBlank()) return
        val maps = Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=${Uri.encode(address)}&mode=d")).apply {
            setPackage("com.google.android.apps.maps")
        }
        try {
            startActivity(maps)
        } catch (_: Exception) {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination=${Uri.encode(address)}&travelmode=driving&dir_action=navigate")))
        }
    }

    private fun setOnline(on: Boolean) {
        runAction { Api.online(this, on) }
        if (!on) stopGps()
    }

    private fun startGps() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestNeededPermissions()
            return
        }
        startForegroundService(Intent(this, LocationService::class.java))
    }

    private fun stopGps() = stopService(Intent(this, LocationService::class.java))

    private fun runAction(startGpsAfter: Boolean = false, call: () -> JSONObject) {
        status.text = "Working…"
        thread {
            try {
                val result = call()
                runOnUiThread {
                    if (startGpsAfter) startGps()
                    status.text = if (result.optBoolean("queued")) "Saved offline — will sync automatically" else "Done"
                    refresh()
                }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Action failed" }
            }
        }
    }

    private fun openReceiptTotal(id: Int) {
        val total = input("Receipt total, e.g. 23.45")
        AlertDialog.Builder(this)
            .setTitle("Finish shopping")
            .setMessage(if (receiptPhoto.isBlank()) "Tip: take a receipt photo first for the proof package." else "Receipt photo attached.")
            .setView(total)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Start delivery") { _, _ ->
                val cents = ((total.text.toString().toDoubleOrNull() ?: -1.0) * 100).toInt()
                if (cents < 0) status.text = "Enter the receipt total" else runAction {
                    if (receiptPhoto.isNotBlank()) Api.proof(this, receiptPhoto = receiptPhoto)
                    Api.shoppingStatus(this, id, "delivering", cents)
                }
            }.show()
    }

    private fun completeDelivery(id: Int, shopping: Boolean) {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(20, 0, 20, 0) }
        val note = input("Delivery note")
        val pin = input("Customer PIN (optional)")
        val signature = input("Customer signature/name (optional)")
        form.addView(note); form.addView(pin); form.addView(signature)
        AlertDialog.Builder(this)
            .setTitle("Complete ${if (shopping) "shopping delivery" else "delivery"}")
            .setMessage(if (proofPhoto.isBlank()) "No proof photo is attached yet. A photo is recommended." else "Proof photo attached.")
            .setView(form)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Complete") { _, _ ->
                runAction {
                    Api.proof(this, note.text.toString(), proofPhoto, receiptPhoto, signature.text.toString(), pin.text.toString())
                    if (shopping) Api.shoppingStatus(this, id, "delivered") else Api.status(this, id, "delivered", note.text.toString(), pin.text.toString())
                }
            }.show()
    }

    private fun openSubstitution() {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(20, 0, 20, 0) }
        val original = input("Unavailable item")
        val replacement = input("Replacement option")
        form.addView(original); form.addView(replacement)
        AlertDialog.Builder(this)
            .setTitle("Offer substitution")
            .setMessage(if (substitutionPhoto.isBlank()) "You can take a substitution photo first, or send without one." else "Substitution photo attached.")
            .setView(form)
            .setNeutralButton("Take photo") { _, _ -> captureMode = "substitution"; openCamera() }
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Send") { _, _ -> runAction { Api.substitution(this, original.text.toString(), replacement.text.toString(), substitutionPhoto) } }
            .show()
    }

    private fun openCamera() {
        try { startActivityForResult(Intent(MediaStore.ACTION_IMAGE_CAPTURE), 91) }
        catch (_: Exception) { status.text = "Camera app unavailable" }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 91 && resultCode == RESULT_OK) {
            val bitmap = data?.extras?.get("data") as? Bitmap ?: return
            val output = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 78, output)
            val encoded = "data:image/jpeg;base64," + Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
            when (captureMode) {
                "proof" -> proofPhoto = encoded
                "receipt" -> receiptPhoto = encoded
                "substitution" -> substitutionPhoto = encoded
            }
            status.text = "Photo attached"
        }
    }

    private fun openMoney() {
        status.text = "Loading money…"
        thread {
            try {
                val money = Api.money(this)
                runOnUiThread {
                    val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(20, 0, 20, 0) }
                    box.addView(label("Available $${"%.2f".format(money.optInt("available_cents") / 100.0)}", 22f, Color.WHITE))
                    box.addView(label("Pending $${"%.2f".format(money.optInt("pending_cents") / 100.0)} · Completed ${money.optInt("completed")}", 14f, muted))
                    box.addView(label("Tracked work miles ${money.optDouble("active_miles")} · Expenses $${"%.2f".format(money.optInt("expense_cents") / 100.0)}", 14f, muted))
                    box.addView(label("Estimated profit $${"%.2f".format(money.optInt("estimated_profit_cents") / 100.0)}", 16f, lime))
                    box.addView(button("Cash out / payout setup", primary = true) { loadPayouts() })
                    box.addView(button("Add expense") { openExpense() })
                    box.addView(button("Download weekly summary") { openUrl(money.optString("weekly_report_url")) })
                    box.addView(button("Download monthly summary") { openUrl(money.optString("monthly_report_url")) })
                    AlertDialog.Builder(this).setTitle("Money").setView(box).setPositiveButton("Close", null).show()
                }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Could not load money" }
            }
        }
    }

    private fun openExpense() {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(20, 0, 20, 0) }
        val amount = input("Amount, e.g. 18.40")
        val note = input("Gas, parking, supplies, etc.")
        form.addView(amount); form.addView(note)
        AlertDialog.Builder(this)
            .setTitle("Add work expense")
            .setView(form)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save") { _, _ ->
                val cents = ((amount.text.toString().toDoubleOrNull() ?: 0.0) * 100).toInt()
                if (cents > 0) runAction { Api.expense(this, cents, "driver_expense", note.text.toString()) }
            }.show()
    }

    private fun loadPayouts() {
        thread {
            try {
                val payout = Api.payout(this)
                runOnUiThread {
                    val available = payout.optInt("available_cents")
                    if (!payout.optBoolean("connected") || !payout.optBoolean("details_submitted") || !payout.optBoolean("transfers_active")) {
                        AlertDialog.Builder(this)
                            .setTitle("Set up payouts")
                            .setMessage("Stripe Express securely collects your payout information. LocalLoop never stores your bank details.")
                            .setNegativeButton("Later", null)
                            .setPositiveButton("Set up") { _, _ -> startPayoutOnboarding() }
                            .show()
                    } else {
                        AlertDialog.Builder(this)
                            .setTitle("Cash out")
                            .setMessage("Available: $${"%.2f".format(available / 100.0)}")
                            .setNegativeButton("Cancel", null)
                            .setPositiveButton(if (available >= 100) "Cash out all" else "OK") { _, _ -> if (available >= 100) cashOutAll() }
                            .show()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Payout error" }
            }
        }
    }

    private fun startPayoutOnboarding() {
        thread {
            try {
                val result = Api.payoutOnboard(this)
                runOnUiThread { openUrl(result.optString("url")) }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Could not start payout setup" }
            }
        }
    }

    private fun cashOutAll() {
        thread {
            try {
                val result = Api.payoutRequest(this)
                runOnUiThread {
                    AlertDialog.Builder(this)
                        .setTitle("Payout sent")
                        .setMessage("$${"%.2f".format(result.optInt("amount_cents") / 100.0)} sent to your Stripe connected account.")
                        .setPositiveButton("OK", null).show()
                    refresh()
                }
            } catch (e: Exception) {
                runOnUiThread { status.text = e.message ?: "Payout failed" }
            }
        }
    }

    private fun openUrl(url: String) {
        if (url.isNotBlank()) startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    }

    private fun logout() {
        stopGps()
        thread {
            try { Api.logout(this) } catch (_: Exception) { }
            Api.clearToken(this)
            runOnUiThread { showEntry() }
        }
    }
}
