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
    "saved": "原图已保存",
}

# Packaging stops and returns export_failed when this many seconds pass.
# Tests and operators can set the env var of the same name to a smaller value.
EXPORT_BUILD_TIMEOUT_S = 120

EXPORT_ZIP_FILENAME = "kids-shiguangji-my-photos.zip"

# User-facing export copy. Tests scan this module for banned words.
EXPORT = {
    "section": "我的数据",
    "row_title": "导出我的照片",
    "row_sub": "只含你上传的照片",
    "badge": "永久免费",
    "free_line": "永久免费",
    "sheet_title": "导出我的照片",
    "sheet_sub": "一键打包，仅你在本家庭上传过的照片。",
    "summary": "将打包你上传的 {count} 张照片",
    "exclude": "不会包含其他家人上传的照片",
    "start": "开始导出",
    "cancel": "取消",
    "packing": "正在打包你的照片…",
    "packing_note": "只含你上传的 · 永久免费",
    "success_title": "打包好了",
    "success_sub": "{count} 张本人上传的照片已打成 ZIP",
    "save_zip": "保存 ZIP",
    "done": "完成",
    "fail_title": "没打包成",
    "fail_body": "网络不稳或服务忙，稍后再试。",
    "retry": "重试",
    "close": "关闭",
    "empty_title": "还没有你上传的照片",
    "empty_body": "批量打包只含本人上传。家人相册里你仍可单张保存原图。",
    "empty_cta": "去时光线保存单张",
    "empty_note": "保存原图 · 永久免费",
    "save_original": "保存原图",
    "save_original_note": "免费 · 家庭成员可用",
    "toast_saved": TOASTS["saved"],
    "zip_filename": EXPORT_ZIP_FILENAME,
}
