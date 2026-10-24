"""Shared roles and the single forbidden-access sentence (M4)."""

FORBIDDEN = "没有访问权限"

ROLES = ("admin", "uploader", "viewer")
ROLE_RANK = {"viewer": 0, "uploader": 1, "admin": 2}
ROLE_LABEL = {
    "admin": "家长管理员",
    "uploader": "可上传",
    "viewer": "只读",
}
ROLE_NOTE = {
    "admin": "家长管理员 · 可邀人、改权限、上传",
    "uploader": "可上传 · 可传照片，不能改别人权限",
    "viewer": "只读 · 只能看",
}

TOASTS = {
    "timeline": "已传到时光线上",
    "album": "已存进你的相册",
    "invite": "邀请已发出",
    "profile": "资料已保存",
    "saved": "原图已下载",
}

# Packaging stops and returns export_failed when this many seconds pass.
# Tests and operators can set the env var of the same name to a smaller value.
EXPORT_BUILD_TIMEOUT_S = 120

# Frontend header wait is timeout_seconds() + this many seconds.
# Constraint: EXPORT_HEADER_MARGIN_S > EXPORT_LOCK_WAIT_S + _STOP_GRACE_S + 1.
EXPORT_HEADER_MARGIN_S = 10

# Short wait so a same-tab retry can take the slot the aborted request is releasing.
EXPORT_LOCK_WAIT_S = 5

# How long POST /api/export/cancel waits for the temp file to be deleted and the slot freed.
EXPORT_CANCEL_WAIT_S = 4

# Blob downloads above this many MiB use the browser's own download. Engineering cap, not a measurement.
EXPORT_BLOB_MAX_MB = 200

# After the zip is finished, another process may still be sending it.
# Startup cleanup keeps files newer than the build timeout plus this send window.
EXPORT_TRANSFER_MAX_S = 3600

# Used when the request omits a valid ?d=YYYY-MM-DD. Env EXPORT_FILENAME_TZ overrides it.
EXPORT_FILENAME_TZ = "Asia/Shanghai"

EXPORT_ZIP_FILENAME = "kids-shiguangji-my-photos.zip"

# User-facing export copy. Keys match spec.md section 2 (mockup COPY). Tests scan this module.
EXPORT = {
    "row_title": "导出我的照片",
    "row_sub_idle": "只含你上传的照片",
    "row_sub_closed": "上次已下载 {md}",
    "row_sub_downloading": "下载中… {p}%",
    "row_sub_done": "上次已下载 · 重新下载",
    "row_sub_failed": "没下载成 · 点这里重试",
    "row_sub_failed_pack": "没打包成 · 点这里看原因",
    "row_sub_failed_pack_retry": "没打包成 · 点这里重试",
    "free": "永久免费",
    "confirm_title": "导出我的照片",
    "confirm_sub": "一键打包，仅你在本家庭上传过的照片。",
    "confirm_sum": "将打包你上传的 {n} 张照片",
    "confirm_sum2": "不会包含其他家人上传的照片",
    "start": "开始导出",
    "cancel": "取消",
    "packing_title": "正在打包你的照片…",
    "packing_sub": "只含你上传的 · 永久免费",
    "packing_hint": "照片多会慢一点，不想等就点「取消」",
    "stopping": "正在停下…",
    "toast_cancelled": "已取消，没留下文件",
    "toast_bg_done": "已下载 · {where}",
    "busy": "上一个导出还没完成，请稍等再试。",
    "packfail_title": "没打包成",
    "packfail_sub": "网络不稳或服务忙，稍后再试。",
    "retry": "重试",
    "close": "关闭",
    "dl_title": "正在下载…",
    "dl_meta": "已收 {a} / {b} MB",
    "dl_collapse": "收起（继续下载）",
    "dl_hint": "收起后还在下载，进度看「我的数据」那一行。别关掉这个页面。",
    "ok_title": "已下载",
    "ok_title_partial": "已下载 {n} 张",
    "ok_sub": "{n} 张你上传的照片都在这个 ZIP 里",
    "where_mobile": "在『文件』App 的下载里",
    "where_desktop": "在『下载』文件夹里",
    "ios_cancel": "如果刚才点了取消，点『重新下载』",
    "k_count": "张数",
    "k_size": "大小",
    "redownload": "重新下载",
    "done": "完成",
    "filename": "时光机-我的照片-{date}.zip",
    "miss_head": "有 {k} 张没打进包",
    "miss_body": "这些原图文件找不到了；如果你手里还有，可以重新上传。",
    "miss_more": "还有 {m} 张，见包里的「缺失清单.txt」",
    "miss_txt": "包里有一份「缺失清单.txt」，写着是哪几张。",
    "miss_item_sub": "时光线里还能看到这条记录",
    "miss_toggle": "按天和人看是哪几张",
    "allmiss_sub": "这 {n} 张照片都没能打进包，所以没有生成文件。",
    "gotit": "知道了",
    "big_title": "已开始下载",
    "big_sub": "已交给浏览器下载。进度和位置看浏览器的下载提示。",
    "fail_title": "没下载成",
    "fail_sub": "还没下载完，点重试重新打包。",
    "empty_up_title": "你还没传过照片，传几张再来导出",
    "empty_up_btn": "去上传",
    "up_title": "照片放到哪里？",
    "up_sub": "选好后再选图。本人照片不会进孩子时光线。",
    "up_a": "挂到孩子时光线",
    "up_a_sub": "按天出现在果果的时光里，署你的名",
    "up_b": "记在我名下",
    "up_b_sub": "进「我的相册」，家庭可见，与孩子内容分开",
    "up_go": "继续选图",
    "upload_sheet_failed": "没传上，再点一次试试",
    "empty_ro_title": "还没有你上传的照片",
    "empty_ro_p": "批量打包只含本人上传。<br>家人相册里你仍可<strong>单张保存原图</strong>。",
    "empty_ro_btn": "去时光线保存单张",
    "empty_ro_free": "保存原图 · 永久免费",
    "v_menu": "保存原图",
    "v_menu_sub": "免费 · 家庭成员可用",
    "toast_photo": "原图已下载",
    "toast_photo_fail": "没下载成，再点一次试试",
}

# Unicode code points, newlines excluded. The write page reads these into data attributes.
JOURNAL_BODY_MAX = 2000
JOURNAL_BODY_WARN = 1900

# Journal and video sentences are re-exported. This file stays the export copy surface.
from app.journal_copy import JOURNAL as JOURNAL
from app.video_copy import VIDEO as VIDEO
