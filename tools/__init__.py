"""The tools shown in the sidebar.

Each tool is a QWidget page built by `factory` ("module:callable") the first time its sidebar
button is clicked; after that it keeps running while other tools are open. A page may have:

  busy_changed = Signal(bool)  the sidebar shows a dot while the tool is working
  is_busy() -> bool            asked before quitting, so a running job isn't cut off silently
                               (a plain bool attribute works too)
  page_shown() / page_hidden() called when the user switches to / away from the page
  closeEvent()                 called when the app quits: save settings, stop workers
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    section: str
    icon: str
    description: str
    factory: str


TOOLS = [
    Tool("photo_enhancer", "Photo Enhancer", "AI Enhance", "sparkles",
         "Enhance and upscale photos with AI (2× / 4×, face and photo restoration) - boostimise.com's enhancer.",
         "tools.photo_enhancer.page:PhotoEnhancerPage"),
    Tool("fetch_by_code", "Fetch by Code", "WhatsApp", "image",
         "Find product photos in your WhatsApp chats by code and save them.",
         "tools.whatsapp_fetch.page:make_by_code"),
    Tool("fetch_by_date", "Fetch by Date", "WhatsApp", "calendar",
         "Save every photo sent in one chat between two dates and times.",
         "tools.fetch_by_date.page:FetchByDatePage"),
    Tool("reaction_list", "Reaction List", "WhatsApp", "list",
         "List the codes reacted with ❌, 📵, 😥 or your own emojis in WhatsApp, with their phone numbers.",
         "tools.reaction_list.page:ReactionListPage"),
    Tool("model_detect", "Model Detect", "WhatsApp", "smartphone",
         "Find each code's photos in WhatsApp and write the phone model next to the code.",
         "tools.model_detect.page:MainWindow"),
    Tool("match_renamer", "Rename by Match", "WhatsApp", "tag",
         "Rename the photos in a folder by matching them with the photos posted in WhatsApp.",
         "tools.match_renamer.page:RenamerPage"),
    Tool("pathao_autofill", "Pathao Auto-fill", "WhatsApp", "truck",
         "Read order messages in WhatsApp and fill Pathao's New Delivery form.",
         "tools.pathao_autofill.page:MainWindow"),
    Tool("photo_match", "PhotoMatch", "Photos", "copy",
         "Find the photos in one folder that are missing from another, then copy them.",
         "tools.photo_match.page:PhotoMatchPage"),
    Tool("snap_rename", "SnapRename", "Photos", "edit",
         "Browse a folder's photos, rename them and drag them into sub-folders.",
         "tools.snap_rename.page:SnapRenamePage"),
    Tool("dashboard", "Dashboard", "Account", "layout-dashboard",
         "Your enhancements today, this week and this month, your plan and history - updated live.",
         "tools.account.dashboard:DashboardPage"),
    Tool("plans", "Plans", "Account", "crown",
         "The free account and the paid plans; request a plan here.",
         "tools.account.plans:PlansPage"),
]
