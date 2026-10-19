package com.covercanvasbd.boostimise.ui

import android.app.Activity
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.DriveFileRenameOutline
import androidx.compose.material.icons.filled.FormatListBulleted
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.LocalShipping
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.WorkspacePremium
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import coil.compose.AsyncImage
import com.covercanvasbd.boostimise.R
import com.covercanvasbd.boostimise.core.Account
import com.covercanvasbd.boostimise.core.AppGuard
import com.covercanvasbd.boostimise.wa.Jobs
import com.covercanvasbd.boostimise.wa.WaEngine
import kotlinx.coroutines.launch

object Nav {
    enum class Screen(val title: String, val icon: ImageVector, val busyKey: String?) {
        ENHANCER("Photo Enhancer", Icons.Filled.AutoAwesome, "enhancer"),
        FETCH_CODE("Fetch by Code", Icons.Filled.Image, "fetch_by_code"),
        REACTION_LIST("Reaction List", Icons.Filled.FormatListBulleted, "reaction_list"),
        RENAME_MATCH("Rename by Match", Icons.Filled.DriveFileRenameOutline, "rename_by_match"),
        MODEL_DETECT("Model Detect", Icons.Filled.Smartphone, "model_detect"),
        PATHAO("Pathao Auto-fill", Icons.Filled.LocalShipping, "pathao"),
        WHATSAPP("WhatsApp", Icons.Filled.Chat, null),
        DASHBOARD("Dashboard", Icons.Filled.Dashboard, null),
        PLANS("Plans", Icons.Filled.WorkspacePremium, null),
    }

    var screen by mutableStateOf(Screen.DASHBOARD)
        private set

    fun go(s: Screen) {
        screen = s
    }
}

@Composable
fun BoostimiseApp(activity: Activity) {
    BoostimiseTheme {
        Surface(Modifier.fillMaxSize().systemBarsPadding(), color = Brand.BG) {
            // a version / phone that may not run shows the update screen, signed in or not
            if (AppGuard.state == AppGuard.State.LOCKED) UpdateScreen(activity)
            else when (Account.state) {
                Account.State.LOADING -> LoadingScreen()
                Account.State.SIGNED_OUT -> SignInScreen(activity)
                Account.State.ONBOARDING -> OnboardingScreen()
                Account.State.BLOCKED -> BlockedScreen()
                Account.State.READY -> Shell(activity)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Shell(activity: Activity) {
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val screen = Nav.screen
    val wa = screen == Nav.Screen.WHATSAPP
    var waTop by remember { mutableIntStateOf(0) }
    val density = LocalDensity.current
    BackHandler(drawer.isOpen) { scope.launch { drawer.close() } }
    BackHandler(!drawer.isOpen && screen != Nav.Screen.DASHBOARD) { Nav.go(Nav.Screen.DASHBOARD) }
    LaunchedEffect(Unit) { WaEngine.open() }

    // The menu opens only with the ☰ button: a swipe that is a bit sideways while scrolling must not open it.
    ModalNavigationDrawer(drawerState = drawer, gesturesEnabled = drawer.isOpen, drawerContent = {
        ModalDrawerSheet {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                    Image(painterResource(R.drawable.logo), null, Modifier.size(36.dp))
                    Spacer(Modifier.width(10.dp))
                    Text("Boost", fontWeight = FontWeight.Bold, fontSize = 20.sp)
                    Text("imise", fontWeight = FontWeight.Bold, fontSize = 20.sp, color = Brand.ACCENT)
                }
                for (s in Nav.Screen.entries) {
                    if (s == Nav.Screen.WHATSAPP || s == Nav.Screen.DASHBOARD) HorizontalDivider(Modifier.padding(vertical = 6.dp))
                    NavigationDrawerItem(
                        icon = { Icon(s.icon, null) },
                        label = { Text(s.title) },
                        badge = {
                            if (s.busyKey != null && s.busyKey in Jobs.busy) Box(Modifier.size(9.dp).background(Brand.SUCCESS, CircleShape))
                            if (s == Nav.Screen.WHATSAPP) Box(Modifier.size(9.dp).background(
                                if (WaEngine.isReady) Brand.SUCCESS else Brand.WARNING, CircleShape))
                        },
                        selected = s == screen,
                        onClick = { Nav.go(s); scope.launch { drawer.close() } },
                        modifier = Modifier.padding(horizontal = 12.dp),
                    )
                }
                HorizontalDivider(Modifier.padding(vertical = 6.dp))
                Row(Modifier.padding(horizontal = 24.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    AsyncImage(Account.displayAvatar(), null, Modifier.size(32.dp).background(Brand.ACCENT_SOFT, CircleShape))
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text(Account.displayName() ?: "", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text(Account.user?.email ?: "", color = Brand.MUTED, fontSize = 12.sp)
                    }
                }
                NavigationDrawerItem(icon = { Icon(Icons.Filled.Logout, null) }, label = { Text("Sign out") }, selected = false,
                    onClick = { Account.signOut() }, modifier = Modifier.padding(horizontal = 12.dp))
                Spacer(Modifier.height(12.dp))
            }
        }
    }) {
        Box(Modifier.fillMaxSize()) {
            // WhatsApp Web stays on screen all the time (behind the tools), so Android never pauses it. Behind a
            // tool it is a 1 dp strip: full size, the whole page was drawn again under every frame of scrolling.
            AndroidView(factory = { WaEngine.view(activity) },
                modifier = if (wa) Modifier.fillMaxSize().padding(top = with(density) { waTop.toDp() })
                else Modifier.fillMaxWidth().height(1.dp))
            Column(Modifier.fillMaxSize().then(if (wa) Modifier else Modifier.background(Brand.BG))) {
                Column(Modifier.background(Brand.BG).onGloballyPositioned { waTop = it.size.height }) {
                    TopAppBar(
                        windowInsets = WindowInsets(0, 0, 0, 0),
                        title = { Text(screen.title) },
                        navigationIcon = { IconButton({ scope.launch { drawer.open() } }) { Icon(Icons.Filled.Menu, "Menu") } },
                        colors = TopAppBarDefaults.topAppBarColors(containerColor = Brand.NAVY, titleContentColor = Color.White,
                            navigationIconContentColor = Color.White),
                    )
                    UpdateBanner(activity)
                    if (wa) WhatsAppHeader()
                }
                if (!wa) Box(Modifier.fillMaxSize().imePadding()) {
                    when (screen) {
                        Nav.Screen.ENHANCER -> EnhancerScreen()
                        Nav.Screen.FETCH_CODE -> FetchScreen(com.covercanvasbd.boostimise.Tools.fetchByCode)
                        Nav.Screen.REACTION_LIST -> ReactionListScreen()
                        Nav.Screen.RENAME_MATCH -> RenameScreen()
                        Nav.Screen.MODEL_DETECT -> ModelDetectScreen()
                        Nav.Screen.PATHAO -> PathaoScreen(activity)
                        Nav.Screen.DASHBOARD -> DashboardScreen()
                        Nav.Screen.PLANS -> PlansScreen()
                        Nav.Screen.WHATSAPP -> Unit
                    }
                }
            }
        }
    }
}
