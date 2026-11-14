"""Daily upload and stamp-grid sentences. Product keys match the Qige final.

Draft notes (anno_*) stay out of this module. Placeholders stand in for
names, counts, and dates so the mock's sample day is not hard-coded.
"""

UPLOAD_DAY_SLIM = {
    "confirm_title": "放到这一天",
    "date_lab": "哪一天",
    "date_today": "今天 · {mo}月{d}日",
    "date_only": "{mo}月{d}日",
    "day_title": "{mo}月{d}日 {weekday}",
    "submit": "放进这一天",
    "submit_self": "放到我的相册",
    "pick_n": "已选 {n} 张",
    "progress": "正在放入 {i}/{n}",
    "fail_one": "{ok} 张已放入，有 {fail} 张没成功",
    "fail_go": "去看已放入的",
    "done_toast": "已放进今天",
    "done_toast_other": "已放进这一天",
    "err_size": "有一张太大了，先拿掉再放",
    "err_bad": "有一张不是照片，已跳过。其余可以继续放。",
    "err_batch": "一次先放五十张就好，多的下次再放",
    "self_banner": "这些照片记在你名下，不会出现在孩子时光线。",
    "self_album": "家人相册 · {name}",
    "count": "共 {n} 张",
    "empty_tl": "还没有照片",
    "empty_cta": "放入第一张",
    "swipe": "左右滑，看这一天",
    "lb_count": "{i} / {n}",
    "lb_open": "看这张",
    "ro_meta": "只读 · 只能看",
    "byline": "{who}传",
    "strip_label": "贴上兄弟姐妹",
    "strip_hint_pre": "已贴{name}，可再贴兄弟姐妹",
}

# The 23 product keys locked 1:1 with the final COPY table.
UPLOAD_DAY_SLIM_FINAL_KEYS = (
    "confirm_title",
    "date_lab",
    "date_today",
    "submit",
    "submit_self",
    "pick_n",
    "progress",
    "fail_one",
    "fail_go",
    "done_toast",
    "err_size",
    "err_bad",
    "self_banner",
    "self_album",
    "count",
    "empty_tl",
    "empty_cta",
    "swipe",
    "lb_count",
    "ro_meta",
    "byline",
    "strip_label",
    "strip_hint_pre",
)
