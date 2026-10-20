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
