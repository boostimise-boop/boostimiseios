package com.covercanvasbd.boostimise

import android.app.Application
import com.covercanvasbd.boostimise.core.Account
import com.covercanvasbd.boostimise.core.AppGuard
import com.covercanvasbd.boostimise.core.Storage
import com.covercanvasbd.boostimise.tools.enhancer.EnhancerTool
import com.covercanvasbd.boostimise.tools.fetch.FetchTool
import com.covercanvasbd.boostimise.tools.model.ModelDetectTool
import com.covercanvasbd.boostimise.tools.pathao.PathaoTool
import com.covercanvasbd.boostimise.tools.reactions.ReactionListTool
import com.covercanvasbd.boostimise.tools.rename.RenameTool
import com.covercanvasbd.boostimise.wa.Jobs
import com.covercanvasbd.boostimise.wa.WaEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

/** The tools live for the whole app, so a tool keeps working while you use another one. */
object Tools {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    lateinit var enhancer: EnhancerTool
    lateinit var fetchByCode: FetchTool
    lateinit var reactionList: ReactionListTool
    lateinit var renameByMatch: RenameTool
    lateinit var modelDetect: ModelDetectTool
    lateinit var pathao: PathaoTool
}

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Storage.init(this)
        Jobs.init(this)
        Account.init(this, Tools.scope)
        AppGuard.init(this, Tools.scope)
        WaEngine.init(this, Tools.scope)
        Tools.enhancer = EnhancerTool(Tools.scope)
        Tools.fetchByCode = FetchTool("fetch_by_code", "Fetch by Code", scope = Tools.scope)
        Tools.reactionList = ReactionListTool(Tools.scope)
        Tools.renameByMatch = RenameTool(Tools.scope)
        Tools.modelDetect = ModelDetectTool(Tools.scope)
        Tools.pathao = PathaoTool(Tools.scope)
        Account.start()
        AppGuard.start()
    }
}
