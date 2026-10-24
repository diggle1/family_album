"""长辈免装文案。51 键与 final mockup COPY 逐字对齐。

稿注 / demo 五键留在这张表里，供键集对照；页面不渲染它们。
"""

ELDERS_DRAFT_KEYS = frozenset(
    {
        "url_key_hint",
        "uploader_no_url_note",
        "readonly_no_copy",
        "entry_url_demo",
        "inv_url_demo",
    }
)

ELDERS_NO_APP = {
    "tab_time": "时光",
    "tab_family": "家人",
    "tab_settings": "设置",
    "ro_meta": "只读 · 只能看",
    "copy_entry": "复制给长辈",
    "slip_to": "亲爱的奶奶：",
    "slip_head": "果果家",
    "slip_tag": "门口笺 · 给长辈",
    "slip_l1": "家里给您留了一份网页相册，不用装任何东西",
    "slip_l2": "请用系统浏览器（Safari/Chrome）打开下面的链接",
    "slip_l3": "打开就能看家里的时光",
    "slip_url_lbl": "入口",
    "slip_inv_lbl": "邀请（还未进家时用）",
    "copy_cta": "复制这张笺",
    "copy_cancel": "先不发",
    "toast_copied": "已复制，发给家人即可",
    "empty_url_hint": "还没填写「给家人的访问地址」，先去设置里填好再复制",
    "empty_url_go": "去填写",
    "url_label": "给家人的访问地址",
    "url_ph": "例如 https://相册.你家域名",
    "url_save": "保存地址",
    "url_saved": "地址已保存",
    "url_help": "长辈用系统浏览器打开这个地址就能看；请填你们实际部署的网址",
    "url_key_hint": "字段键 family_access_url · 仅管理员可见可编",
    "inv_title": "请来看家里的相册",
    "inv_family": "果果家",
    "inv_role": "您的角色：只看、不改——可以看照片、日志和视频，不能上传或改。",
    "inv_accept": "接受邀请",
    "inv_login": "已有账号登录",
    "inv_note": "用系统浏览器打开后点接受。接受后登录或设个密码，就能进时光线。",
    "inv_expired_title": "这张邀请过期了",
    "inv_expired_sub": "请让家人再发一次邀请。已进家的长辈仍可用网页入口。",
    "bad_title": "打不开这页",
    "bad_sub": "链接不对或已失效。家里的内容不会在这里显示。",
    "tip_wechat": "想下次好找：点右上角 ··· → 收藏",
    "tip_safari": "想下次好找：添加到主屏幕或书签",
    "tip_ok": "知道了",
    "mem_section": "家人",
    "mem_role_admin": "管理员",
    "mem_role_up": "可上传",
    "mem_role_ro": "只读",
    "mem_pending": "待接受",
    "settings_title": "设置",
    "tl_hint_ro": "只读 · 只能看",
    "paste_body": (
        "亲爱的奶奶：\n\n"
        "家里给您留了一份网页相册，不用装任何东西。\n"
        "请用系统浏览器（Safari/Chrome）打开下面的链接。\n"
        "打开就能看家里的时光。\n\n"
        "家庭：果果家\n"
        "入口：https://album.guoguo.home\n"
        "邀请（还未进家）：https://album.guoguo.home/i/a8f3"
    ),
    "paste_inv_line": "邀请（还未进家）：{inv}",
    "err_403": "你没有复制权限",
    "uploader_no_url_note": "可上传设置页不展示「给家人的访问地址」",
    "readonly_no_copy": "只读长辈无「复制给长辈」入口",
    "entry_url_demo": "https://album.guoguo.home",
    "inv_url_demo": "https://album.guoguo.home/i/a8f3",
}
