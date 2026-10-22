package com.boostimise.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// The logo's colours (core/theme.py): navy "B", blue photo and sparkles.
object Brand {
    val NAVY = Color(0xFF0F1D3A)
    val SKY = Color(0xFF1EA0FF)
    val ACCENT = Color(0xFF0A6CF0)
    val ACCENT_SOFT = Color(0xFFE8F2FF)
    val ACCENT_DARK = Color(0xFF0A46A8)
    val BG = Color(0xFFF3F6FB)
    val SURFACE = Color(0xFFFFFFFF)
    val BORDER = Color(0xFFE1E7F0)
    val TEXT_2 = Color(0xFF53607A)
    val MUTED = Color(0xFF8B96AB)
    val SUCCESS = Color(0xFF16A34A)
    val WARNING = Color(0xFFC27803)
    val ERROR = Color(0xFFDC2626)
    // soft backgrounds (icon squares, status squares) and the darker text that reads on them
    val SUCCESS_SOFT = Color(0xFFE7F6EC)
    val SUCCESS_DARK = Color(0xFF137A39)
    val WARNING_SOFT = Color(0xFFFFF3DC)
    val WARNING_DARK = Color(0xFF8A5300)
    val ERROR_SOFT = Color(0xFFFDECEC)
    val ERROR_DARK = Color(0xFFB91C1C)
    val NEUTRAL_SOFT = Color(0xFFEEF1F6)
    val LINE = Color(0xFFE6EBF3)          // card outlines
    val TRACK = Color(0xFFF1F4F9)         // code chips
    // on the navy control card
    val ON_NAVY = Color(0xFFC6D2E8)
    val LIVE = Color(0xFF34D399)          // working, saved
    val AMBER_ON_NAVY = Color(0xFFF2B544)
    val RED_ON_NAVY = Color(0xFFF87171)
}

@Composable
fun BoostimiseTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Brand.ACCENT,
            onPrimary = Color.White,
            primaryContainer = Brand.ACCENT_SOFT,
            onPrimaryContainer = Brand.ACCENT_DARK,
            secondary = Brand.NAVY,
            onSecondary = Color.White,
            background = Brand.BG,
            onBackground = Brand.NAVY,
            surface = Brand.SURFACE,
            onSurface = Brand.NAVY,
            surfaceVariant = Color(0xFFF7F9FC),
            onSurfaceVariant = Brand.TEXT_2,
            outline = Color(0xFFCDD6E4),
            outlineVariant = Brand.BORDER,
            error = Brand.ERROR,
        ),
        content = content,
    )
}
