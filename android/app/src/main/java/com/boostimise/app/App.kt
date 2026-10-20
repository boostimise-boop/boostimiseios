package com.boostimise.app

import android.app.Application
import com.boostimise.app.core.Account
import com.boostimise.app.core.AppGuard
import com.boostimise.app.core.Storage
import com.boostimise.app.tools.enhancer.EnhancerTool
import com.boostimise.app.tools.fetch.FetchTool
import com.boostimise.app.tools.model.ModelDetectTool
import com.boostimise.app.tools.pathao.PathaoTool
import com.boostimise.app.tools.reactions.ReactionListTool
import com.boostimise.app.tools.rename.RenameTool
import com.boostimise.app.wa.Jobs
import com.boostimise.app.wa.WaEngine
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
