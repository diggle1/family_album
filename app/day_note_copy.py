"""User-visible sentences for one sentence a day. Keys match the final COPY table.

Draft notes (anno_*) stay out of the product constant. The page reads
DAY_NOTE through data attributes. Scripts do not embed these sentences.
"""

DAY_NOTE_BODY_DISPLAY = 80
DAY_NOTE_BODY_WARN = 70
DAY_NOTE_BODY_MAX = 200
DAY_NOTE_SNIP = 40

# Shown when the child row is already gone and there is no name column to keep.
DAY_NOTE_CHILD_GONE = "已移出的孩子"

DAY_NOTE = {
    "tab_time": "时光",
    "tab_family": "家人",
    "tab_settings": "设置",
    "ro_meta": "只读 · 只能看",
    "day_title": "{mo}月{d}日",
    "day_meta": "{y}年 · {wd} · {age}",
    "sec_journal": "这一天的日志",
    "sec_photos": "这一天的照片",
    "sec_notes": "这一天的一句",
    "entry_write": "写一句",
    "entry_edit": "改一句",
    "w_title_new": "写一句",
    "w_title_edit": "改一句",
    "w_placeholder": "今天想对孩子说的一句话",
    "w_count": "{n}/80",
    "w_submit": "留下这句",
    "w_submit_edit": "改好了",
    "sib_label": "写给谁",
    "sib_hint_pre": "已选{name}，可再点兄弟姐妹",
    "sib_hint_pick": "点头像选择，至少一位",
    "card_by": "{who}",
    "j_quote": "今天幼儿园画了大太阳",
    "j_sub": "妈妈写 · 写给果果",
    "note_1": "果果今天自己穿好了鞋子，真棒。",
    "note_2": "外婆看了你画的太阳，笑了好久。",
    "note_3": "放学路上说最喜欢蓝色的天",
    "snip_line": "{who}：{text}",
    "err_empty": "先写几个字再留",
    "err_too_long": "有点长，缩短一点再留",
    "err_no_child": "先选写给哪个孩子",
    "err_perm": "你没有权限做这件事",
    "err_perm_sub": "只读家人可以看这一句，不能写。",
    "deny_title": "进不了这家",
    "deny_sub": "您不在这家的成员里，这里不会显示内容。",
    "empty_no_slot": "无人写 → 暖纸签不渲染、不催更",
    "tl_family": "果果家",
    "tl_family_all": "全家",
    "age_chip": "3岁2个月",
    "age_fmt": "3岁2个月",
    "wd_sun": "周日",
    "name_gg": "果果",
    "name_dd": "弟弟",
    "name_mama": "妈妈",
    "name_baba": "爸爸",
    "name_yeye": "外婆",
    "desk_aside": "电脑和手机看到的是同一本相册",
    "url_demo": "album.guoguo.home / 某一天",
    "form_hint": "≤80 字 · 不附图 · 不@",
}

DAY_NOTE_ANNO = {
    "anno_warm": "暖纸短签 · 浅纸+左竖 · 非撕边脚注",
    "anno_multi": "多作者各一条 · 同天同作者一条",
    "anno_empty": "无人写 → 不占位；可写见入口",
    "anno_ro": "只读长辈 · 见句无写入口",
    "anno_snip": "日卡有句 → 露一行摘要",
    "anno_plain": "无句 → 日卡不变、不空壳",
    "anno_desk": "桌面内容柱 · 写→见主路径",
    "anno_form": "页内轻表单 · 大输入+兄弟姐妹条",
}
