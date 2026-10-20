package com.boostimise.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.boostimise.app.Tools
import com.boostimise.app.core.Account
import com.boostimise.app.core.formatResetIn
import com.boostimise.app.tools.enhancer.EnhancerApi
import com.boostimise.app.tools.enhancer.QueueItem
import com.boostimise.app.tools.enhancer.isStartable
import com.boostimise.app.tools.enhancer.presetFor

private val MODES = listOf("auto" to "Auto", "face" to "Face", "restore" to "Restore", "upscale" to "Upscale")
private val STAGES = mapOf("analysis" to "Analysis", "face_detection" to "Face detection", "face_restoration" to "Face restoration",
    "upscaling" to "Upscaling", "finalizing" to "Color / sharpen", "saving" to "Saving", "queued" to "In queue",
    "waiting" to "Waiting", "uploading" to "Uploading")

@Composable
fun EnhancerScreen() {
    val t = Tools.enhancer
    val ctx = LocalContext.current
    DisposableEffect(Unit) {
        t.startHealth()
        onDispose { t.stopHealth() }
    }
    LaunchedEffect(t.toast) {
        if (t.toast.isNotEmpty()) {
            toast(ctx, t.toast)
            t.toast = ""
        }
    }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(50)) { uris -> if (uris.isNotEmpty()) t.add(uris) }
    val launchPick = { pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
    Page {
        val q = Account.quota
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(if (q?.limit == null) "Unlimited enhancements today" else "${q.remaining ?: 0} of ${q.limit} left today" +
                if (q.resetsAt.isNotEmpty()) " · resets in ${formatResetIn(q.resetsAt)}" else "", Modifier.weight(1f), fontSize = 13.sp,
                color = Brand.TEXT_2)
            if (t.offline) Text("Server offline", color = Brand.ERROR, fontSize = 13.sp)
        }
        if (t.items.isEmpty()) {
            Section {
                Column(Modifier.fillMaxWidth().padding(vertical = 24.dp), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("AI Photo Enhancer", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                    Hint("Upscale 2× / 4×, restore faces and old photos. JPG, PNG or WEBP, up to ${t.maxUploadMb().toInt()} MB.")
                    Button(launchPick) { Text("Upload Images") }
                    Hint("Or share photos from WhatsApp or the gallery to Boostimise.")
                }
            }
            return@Page
        }
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(t.items, key = { it.id }) { it -> Thumb(it, it.id == t.selected?.id) { t.selectedId = it.id; t.ensureAnalysis(it) } }
            item {
                Box(Modifier.size(72.dp).border(1.dp, Brand.BORDER, RoundedCornerShape(10.dp)).clickable { launchPick() },
                    contentAlignment = Alignment.Center) { Text("+", fontSize = 28.sp, color = Brand.ACCENT) }
            }
        }
        val sel = t.selected ?: return@Page
        LaunchedEffect(sel.id) { t.ensureAnalysis(sel) }
        Section {
            Compare(sel)
            Text(sel.name, fontWeight = FontWeight.Medium, fontSize = 14.sp)
            val dims = sel.dims
            Hint((dims?.let { "${it.first}×${it.second} · " } ?: "") + "%.1f MB".format(sel.size / 1048576.0) + " · " +
                (STAGES[sel.stage] ?: sel.status))
            if (sel.status == "uploading") LinearProgressIndicator(progress = { sel.upload }, modifier = Modifier.fillMaxWidth())
            if (sel.status == "processing" || sel.status == "queued") LinearProgressIndicator(progress = { sel.progress / 100f }, modifier = Modifier.fillMaxWidth())
            sel.error?.let { Hint(it, Brand.ERROR) }
            if (sel.status == "done") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button({ t.download(sel, "png") }, enabled = t.downloading.isEmpty() && sel.job?.optBoolean("png_ready", true) != false) { Text("Download PNG") }
                    OutlinedButton({ t.download(sel, "jpg") }, enabled = t.downloading.isEmpty()) { Text("JPG") }
                }
                val result = sel.job?.optJSONObject("result")
                result?.optJSONObject("benchmark")?.let { b ->
                    Hint("${b.optString("input")} → ${b.optString("output")} · ${"%.1f".format(b.optDouble("total_seconds", 0.0))} s" +
                        if (b.optInt("faces_detected") > 0) " · ${b.optInt("faces_restored")} of ${b.optInt("faces_detected")} faces restored" else "")
                }
            }
            Row { TextButton({ t.remove(sel) }) { Text("Remove") }; TextButton({ t.clear() }) { Text("Remove all") } }
        }
        Analysis(t.analysisOf(sel))
        Settings()
        val startable = t.items.count { isStartable(it.status) }
        Button({ t.enhance(onlySelected = true) }, Modifier.fillMaxWidth(),
            enabled = !t.offline && (if (t.applyAll) startable > 0 else isStartable(sel.status))) {
            Text(if (t.applyAll && startable > 1) "ENHANCE $startable PHOTOS" else "ENHANCE PHOTO")
        }
        CheckRow("Enhance all photos at once", t.applyAll) { t.applyAll = it }
        if (t.items.count { it.status == "done" } > 1) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton({ t.downloadZip("jpg") }, enabled = t.downloading.isEmpty()) { Text("All as JPG (.zip)") }
            OutlinedButton({ t.downloadZip("png") }, enabled = t.downloading.isEmpty()) { Text("All as PNG (.zip)") }
        }
        if (t.downloading.isNotEmpty()) Hint("Downloading…")
        Hint("Downloads go to Pictures/Boostimise/Enhanced (ZIPs to Download/Boostimise).")
    }
    if (t.denied.isNotEmpty()) LimitDialog(t.denied) { t.denied = "" }
}

@Composable
private fun Thumb(it: QueueItem, selected: Boolean, onClick: () -> Unit) {
    Box(Modifier.size(72.dp).clip(RoundedCornerShape(10.dp))
        .border(if (selected) 3.dp else 1.dp, if (selected) Brand.ACCENT else Brand.BORDER, RoundedCornerShape(10.dp))
        .clickable(onClick = onClick)) {
        AsyncImage(it.file, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        val c = when (it.status) {
            "done" -> Brand.SUCCESS
            "failed", "cancelled" -> Brand.ERROR
            "idle" -> null
            else -> Brand.ACCENT
        }
        if (c != null) Box(Modifier.align(Alignment.BottomEnd).padding(4.dp).size(10.dp).background(c, RoundedCornerShape(5.dp)))
    }
}

/** Before / after: drag the divider. */
@Composable
private fun Compare(it: QueueItem) {
    var split by remember(it.id) { mutableFloatStateOf(0.5f) }
    val ratio = it.dims?.let { d -> d.first.toFloat() / d.second } ?: 1f
    val job = it.job
    Box(Modifier.fillMaxWidth().aspectRatio(ratio.coerceIn(0.5f, 2f)).clip(RoundedCornerShape(10.dp)).background(Color(0xFF111827))) {
        AsyncImage(it.file, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
        if (it.status == "done" && job != null) {
            var width by remember { mutableFloatStateOf(1f) }
            AsyncImage(EnhancerApi.resultUrl(job, "preview"), null, Modifier.fillMaxSize()
                .drawWithContent {
                    width = size.width
                    clipRect(left = size.width * split) { this@drawWithContent.drawContent() }
                }
                .pointerInput(Unit) { detectDragGestures { c, _ -> split = (c.position.x / width).coerceIn(0f, 1f) } },
                contentScale = ContentScale.Fit)
            Box(Modifier.fillMaxHeight().width(2.dp).align(Alignment.CenterStart)
                .offset { androidx.compose.ui.unit.IntOffset((width * split).toInt(), 0) }.background(Color.White))
            Text("Before", Modifier.align(Alignment.TopStart).padding(6.dp), color = Color.White, fontSize = 11.sp)
            Text("After", Modifier.align(Alignment.TopEnd).padding(6.dp), color = Color.White, fontSize = 11.sp)
        }
    }
}

@Composable
private fun Analysis(data: org.json.JSONObject?) {
    data ?: return
    val a = data.optJSONObject("analysis") ?: return
    val faces = data.optJSONArray("faces")?.length() ?: 0
    Section("AI analysis") {
        Hint("quality ${a.optDouble("quality_score", 0.0).toInt()}/100 · ${a.optInt("width")}×${a.optInt("height")} · " +
            "$faces face${if (faces == 1) "" else "s"}" + if (a.optBoolean("grayscale")) " · B&W" else "")
        for ((name, v) in listOf("Blur" to ((a.optDouble("blur", 0.0) - .3) / .45).coerceIn(0.0, 1.0), "Noise" to a.optDouble("noise", 0.0),
            "Compression" to a.optDouble("jpeg_artifacts", 0.0), "Low resolution" to a.optDouble("low_resolution", 0.0))) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(name, Modifier.width(110.dp), fontSize = 12.sp)
                LinearProgressIndicator(progress = { v.toFloat().coerceIn(0f, 1f) }, modifier = Modifier.weight(1f))
            }
        }
        data.optJSONObject("plan")?.optJSONArray("reasons")?.let { r -> for (i in 0 until r.length()) Hint("✓ ${r.optString(i)}") }
    }
}

@Composable
private fun Settings() {
    val t = Tools.enhancer
    val s = t.settings
    var advanced by remember { mutableStateOf(false) }
    Section("Settings") {
        Chips {
            for ((k, label) in MODES) {
                if (k == s.mode) Button({}) { Text(label) } else OutlinedButton({ t.settings = presetFor(k, s) }) { Text(label) }
            }
        }
        Chips {
            for (sc in listOf(2, 4)) if (sc == s.scale) Button({}) { Text("${sc}×") } else OutlinedButton({ t.settings = s.copy(scale = sc) }) { Text("${sc}×") }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Face restoration", Modifier.weight(1f))
            Switch(s.faceEnhance, { t.settings = s.copy(faceEnhance = it) })
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Colour enhance", Modifier.weight(1f))
            Switch(s.colorEnhance, { t.settings = s.copy(colorEnhance = it) })
        }
        TextButton({ advanced = !advanced }) { Text(if (advanced) "Hide advanced" else "Advanced") }
        if (advanced) {
            if (s.faceEnhance) Select("Face model", listOf("auto" to "Auto (recommended)", "codeformer" to "Balanced",
                "gfpgan" to "Smooth", "compare" to "Compare"), s.faceModel) { t.settings = s.copy(faceModel = it) }
            Text("Strength ${"%.2f".format(s.strength)}", fontSize = 13.sp)
            Slider(s.strength.toFloat(), { t.settings = s.copy(strength = it.toDouble()) }, valueRange = 0f..1f)
            Auto("Denoise", s.denoise) { t.settings = s.copy(denoise = it) }
            Auto("Sharpen", s.sharpen) { t.settings = s.copy(sharpen = it) }
        }
    }
}

@Composable
private fun Auto(label: String, value: Double?, onChange: (Double?) -> Unit) {
    CheckRow("$label: automatic", value == null) { onChange(if (it) null else 0.5) }
    if (value != null) Slider(value.toFloat(), { onChange(it.toDouble()) }, valueRange = 0f..1f)
}

@Composable
fun LimitDialog(reason: String, onClose: () -> Unit) {
    val q = Account.quota
    AlertDialog(onDismissRequest = onClose,
        confirmButton = { TextButton({ onClose(); if (reason == "limit") Nav.go(Nav.Screen.PLANS) }) { Text(if (reason == "limit") "See plans" else "OK") } },
        dismissButton = { TextButton(onClose) { Text("Close") } },
        title = { Text(if (reason == "blocked") "Account suspended" else "Daily limit reached") },
        text = {
            Text(if (reason == "blocked") "Your account has been suspended."
            else "You've used today's enhancements" + (q?.resetsAt?.takeIf { it.isNotEmpty() }?.let { " - more in ${formatResetIn(it)}" } ?: "") +
                ". A paid plan gives you more every day.")
        })
}
