package com.boostimise.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AutoFixHigh
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Remove
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.boostimise.app.wa.WaEngine
import kotlin.math.ceil
import kotlin.math.max

/*
 * The look of the Windows app's tool pages (core/look.py) on the phone:
 *   ControlCard   the navy card: a progress ring, what is happening, the counts and the buttons
 *   HeroButton    a button on it (start: sky blue; the others see-through white)
 *   StatusRow     a result: its status square, the code as a chip, the name and the status in its colour
 *   IconSquare    a card's icon on a soft square (Section in Widgets.kt draws the white cards)
 */

/** An icon square's colours: soft background and icon. */
enum class Tint(val bg: Color, val fg: Color) {
    ACCENT(Brand.ACCENT_SOFT, Brand.ACCENT),
    SUCCESS(Brand.SUCCESS_SOFT, Brand.SUCCESS),
    WARNING(Brand.WARNING_SOFT, Brand.WARNING),
    NEUTRAL(Brand.NEUTRAL_SOFT, Brand.NAVY),
}

@Composable
fun IconSquare(icon: ImageVector, tint: Tint, size: Dp = 36.dp) {
    Box(Modifier.size(size).background(tint.bg, RoundedCornerShape(size * 0.3f)), contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(size / 2), tint = tint.fg)
    }
}

/** A result's kind ("" | working | ok | none | error | skipped): soft background, text, strong colour and icon. */
class KindLook(val soft: Color, val text: Color, val strong: Color, val icon: ImageVector)

fun kindLook(kind: String) = when (kind) {
    "ok" -> KindLook(Brand.SUCCESS_SOFT, Brand.SUCCESS_DARK, Brand.SUCCESS, Icons.Rounded.Check)
    "working" -> KindLook(Brand.ACCENT_SOFT, Brand.ACCENT_DARK, Brand.ACCENT, Icons.Rounded.AutoFixHigh)
    "none" -> KindLook(Brand.WARNING_SOFT, Brand.WARNING_DARK, Brand.WARNING, Icons.Rounded.ErrorOutline)
    "error" -> KindLook(Brand.ERROR_SOFT, Brand.ERROR_DARK, Brand.ERROR, Icons.Rounded.ErrorOutline)
    "skipped" -> KindLook(Brand.NEUTRAL_SOFT, Brand.TEXT_2, Brand.MUTED, Icons.Rounded.Remove)
    else -> KindLook(Brand.BG, Brand.TEXT_2, Brand.MUTED, Icons.Rounded.Schedule)
}

/** The kinds of a finished code (the ring's "done"). */
val DONE_KINDS = setOf("ok", "none", "error", "skipped")

fun plural(n: Int, word: String) = "$n $word" + if (n == 1) "" else "s"

/** The control card's top line for a WhatsApp tool, and its colour. */
fun waKicker(running: Boolean): Pair<String, Color> = when {
    running -> "WORKING" to Brand.LIVE
    else -> when (WaEngine.phase) {
        WaEngine.Phase.READY -> "CONNECTED" to Brand.SKY
        WaEngine.Phase.LOADING -> "CONNECTING" to Brand.SKY
        WaEngine.Phase.QR -> "SCAN THE QR CODE" to Brand.AMBER_ON_NAVY
        WaEngine.Phase.ERROR -> "WHATSAPP ERROR" to Brand.RED_ON_NAVY
        WaEngine.Phase.OFF -> "NOT CONNECTED" to Brand.ON_NAVY
    }
}

/** The ring: the share drawn (null: busy, a quarter), the big text and the unit under it. */
class RingState(val share: Float?, val text: String, val unit: String)

/** "4/6 codes"; "0" before there is anything; busy while running without a total yet. */
fun ringOf(done: Int, total: Int, unit: String, running: Boolean = false) = when {
    total > 0 -> RingState(done.toFloat() / total, "$done/$total", unit)
    running -> RingState(null, "…", unit)
    else -> RingState(0f, "0", unit)
}

/** One count of the control card: a big number over a coloured dot and its name. */
class Count(val label: String, val value: Int, val color: Color)

/**
 * The navy control card. Its counts sit beside the status when the card is wide, else under it in
 * a grid as even as the width allows; the buttons wrap onto more lines when they don't fit.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ControlCard(kicker: Pair<String, Color>, title: String, sub: String = "", ring: RingState,
                counts: List<Count> = emptyList(), buttons: @Composable () -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth().background(Brand.NAVY, RoundedCornerShape(22.dp)).padding(16.dp)) {
        val width = maxWidth
        val wide = counts.isNotEmpty() && width >= 320.dp + 120.dp * counts.size
        Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Ring(ring)
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(kicker.first, color = kicker.second, fontSize = 11.sp, fontWeight = FontWeight.ExtraBold,
                        letterSpacing = 1.sp)
                    Text(title, color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.Bold, lineHeight = 21.sp)
                    if (sub.isNotEmpty()) Text(sub, color = Brand.ON_NAVY, fontSize = 12.5.sp, lineHeight = 16.sp)
                }
                if (wide) {
                    Spacer(Modifier.width(16.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        for (c in counts) CountTile(c, Modifier.widthIn(min = 96.dp))
                    }
                }
            }
            if (!wide && counts.isNotEmpty()) CountGrid(counts, width)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                buttons()
            }
        }
    }
}

@Composable
private fun Ring(ring: RingState) {
    Box(Modifier.size(64.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 7.dp.toPx()
            val inset = stroke / 2 + 1.dp.toPx()
            val box = Size(size.width - inset * 2, size.height - inset * 2)
            drawArc(color = Color.White.copy(alpha = 0.12f), startAngle = 0f, sweepAngle = 360f, useCenter = false,
                topLeft = Offset(inset, inset), size = box, style = Stroke(stroke))
            val share = (ring.share ?: 0.25f).coerceIn(0f, 1f)
            if (share > 0f) drawArc(color = Brand.SKY, startAngle = -90f, sweepAngle = 360f * share, useCenter = false,
                topLeft = Offset(inset, inset), size = box, style = Stroke(stroke, cap = StrokeCap.Round))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(ring.text, color = Color.White, fontSize = if (ring.text.length <= 5) 16.sp else 12.sp,
                fontWeight = FontWeight.ExtraBold, lineHeight = 18.sp, maxLines = 1)
            Text(ring.unit, color = Brand.ON_NAVY, fontSize = 9.5.sp, lineHeight = 11.sp, maxLines = 1)
        }
    }
}

/**
 * The counts in rows of equal tiles, each wide enough for the longest name: 4 counts that don't fit in one
 * row become 2 × 2, not 3 + 1.
 */
@Composable
private fun CountGrid(counts: List<Count>, width: Dp) {
    val measurer = rememberTextMeasurer()
    val label = with(LocalDensity.current) {
        counts.maxOf { measurer.measure(it.label, TextStyle(fontSize = 12.sp)).size.width }.toDp()
    }
    val fit = max(1, ((width + 8.dp) / (label + TILE_EXTRA + 8.dp)).toInt())
    val perRow = if (fit >= counts.size) counts.size else ceil(counts.size / ceil(counts.size / fit.toDouble())).toInt()
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (line in counts.chunked(perRow)) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (c in line) CountTile(c, Modifier.weight(1f))
            repeat(perRow - line.size) { Spacer(Modifier.weight(1f)) }
        }
    }
}

/** A count tile's width besides its name: the padding, the dot and the gap after it. */
private val TILE_EXTRA = 30.dp

@Composable
private fun CountTile(c: Count, modifier: Modifier) {
    Column(modifier.background(Color.White.copy(alpha = 0.07f), RoundedCornerShape(14.dp))
        .padding(horizontal = 9.dp, vertical = 8.dp)) {
        Text("${c.value}", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, lineHeight = 24.sp)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).background(c.color, CircleShape))
            Spacer(Modifier.width(5.dp))
            Text(c.label, color = Brand.ON_NAVY, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** A button on the control card: [primary] is sky blue (Start), [outline] a white outline (Stop). */
@Composable
fun HeroButton(text: String, icon: ImageVector? = null, primary: Boolean = false, outline: Boolean = false,
               enabled: Boolean = true, onClick: () -> Unit) {
    Button(onClick, enabled = enabled, shape = RoundedCornerShape(12.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = when {
                primary -> Brand.SKY
                outline -> Color.Transparent
                else -> Color.White.copy(alpha = 0.10f)
            },
            contentColor = if (primary) Brand.NAVY else Color.White,
            disabledContainerColor = if (primary) Color(0xFF1D4F86) else Color.White.copy(alpha = 0.05f),
            disabledContentColor = Color(0xFF6F86AB)),
        border = if (outline) BorderStroke(1.dp, Color.White.copy(alpha = if (enabled) 0.3f else 0.14f)) else null,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp)) {
        if (icon != null) {
            Icon(icon, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
        }
        Text(text, fontSize = 14.sp, fontWeight = if (primary) FontWeight.ExtraBold else FontWeight.Bold, maxLines = 1)
    }
}

/** A code as a small grey chip in a monospaced font. */
@Composable
fun CodeChip(code: String) {
    Text(code, Modifier.background(Brand.TRACK, RoundedCornerShape(6.dp)).padding(horizontal = 7.dp, vertical = 2.dp),
        color = Brand.ACCENT_DARK, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 12.sp,
        maxLines = 1)
}

/**
 * A result as a row: the status square of its [kind] ([badge], an emoji, in place of the icon), the
 * code chip and the name, the status in the kind's colour, then any [more] lines and the [trailing] action.
 */
@Composable
fun StatusRow(kind: String, code: String, name: String = "", status: String = "", badge: String? = null,
              more: List<String> = emptyList(), trailing: (@Composable () -> Unit)? = null) {
    val k = kindLook(kind)
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(36.dp).background(k.soft, RoundedCornerShape(11.dp)), contentAlignment = Alignment.Center) {
            // two emojis or more get smaller to fit the square
            if (badge != null) Text(badge, fontSize = if (badge.codePointCount(0, badge.length) > 1) 11.sp else 15.sp, maxLines = 1)
            else Icon(k.icon, null, Modifier.size(18.dp), tint = k.strong)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (code.isNotEmpty()) CodeChip(code)
                if (code.isNotEmpty() && name.isNotEmpty()) Spacer(Modifier.width(8.dp))
                if (name.isNotEmpty()) Text(name, color = Brand.NAVY, fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (status.isNotEmpty()) Text(status, color = k.text, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
            for (m in more) if (m.isNotEmpty()) Text(m, color = Brand.TEXT_2, fontSize = 12.sp)
        }
        trailing?.invoke()
    }
    HorizontalDivider(color = Brand.NEUTRAL_SOFT)
}
