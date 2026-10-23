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
}
