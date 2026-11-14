# Tech · 深链确认页（窄）就地签 · v2026.11.15.1

**状态：** 实现前审稿（本刀只落盘 tech，**不开 CloudAgent**、**不开 PR**、**不 clone**）。  
**版本：** `v2026.11.15.1`  
**tech 实测全量 md5：** 以本文件磁盘 `md5sum` 为准（见同目录 `2026-11-14-upload-kindless-slim-tech.md5`）。  
**基线 main tip：`cfe831a53f7d` = tag `v2026.11.14.2`（底签已上，线上已部署）**（监工给定；box 无完整 clone，事实以 tip 语义 + cut/final + GitHub 读 tip 文件为准；**实现时对照 main tip `cfe831a53f7d5619c18d629f50ed26461a69a301`** 的 `app/templates/upload.html` / `timeline.html` / `fragments/stage_inner.html` / `fragments/upload_slip.html` / `app/static/upload_day_slim.js` / `app/upload_day_slim_copy.py` / `app/routers/pages.py` / `app/static/app.js` / `app.css` / `tests/test_upload_confirm_slim.py` / `tests/test_upload_day_slim.py`）。  
**回退目标：`v2026.11.14.2`**（tip `cfe831a53f7d`；本刀**零新表零新列**；回退**不 DROP**；**禁** `docker compose down -v`）。不动已上线 `.2` 的历史 tag。若 `.2` 自身还要再回，仍走既有 `v2026.11.14.1`，不是本刀的事。  
**cut：** `/workspace/pm/family_album/2026-11-14-深链确认页-cut.md`（**定稿** 2026-11-14 19:10 Asia/Shanghai，超时补锁；文末「## 设计 final」已于其后补齐，PM 已声明锁体未改）。  
**cut 身份锁（锁体，认据 · In/AC）：** `7a7542a375983de994334194a2c16b8f`（短 `7a7542a3`；19:10 定稿正文，**不含**「## 设计 final」）。  
**cut 全文件（含「## 设计 final」指针）：** `16bc028833f45c65809340c9e389d246`（短 `16bc0288`）。引用 cut 时**两份哈希一起写**。附录只指向已核 final，不改 In/AC。  
**依据草稿：** `2026-11-14-深链确认页-pm2.md` md5 `eb3a268bd70217c2349e6a96044c061c`。  
**final：** `/workspace/design/family_album/upload-kindless-slim/final/` · mockup 实测全量 md5 **`cc7239a37a13a5cd7ea694eb20e8fce9`**（短 `cc7239a3`；合稿名 **「就地签」**；designer 19:33 Asia/Shanghai **已核 / PASS**，核后只盖章文档，`mockup.html` 未改；监工 19:34 可交工程）。  
过程稿 `v-designer/`「就地底签」`7f0d49857ef8f2e79ad34a4093eaeb7d` / `v-designer2/`「就地签」`db19691cca381d58056f1ff1fad3d251` **不作工程认据**。脚下签 `ffaf1ef8…` **不作工程认据**。  
已核底签仍是 `upload-confirm-slim/final` **`6e6a9099c844ed836e3c89cdafe4211f`**（短 `6e6a9099`），本刀**不改形态**。已核齐格签仍是 `upload-day-slim/final` **`6f2a5be00e2c35a9f1816c3a20ce283f`**（短 `6f2a5be0`），进度签 / 天页齐格 / 时光线小排原样继承，不重做。  
结构模板：`2026-11-14-upload-confirm-slim-tech.md`（文首门禁表、失败路径扩表、§10 全关）。本刀只收无 kind 深链入口，底签本身不另做一套。  
**今晚不上。** 未过 tech-r1 不开 CloudAgent；过审后也只交 PR，**禁 merge / tag / 部署**至次日流水线另令。微信小程序（含手机号一键登录）仍停。

**final 全量实测 md5（落盘前 2026-11-14 19:38 Asia/Shanghai）：**

| 文件 | 全量 md5 | 短 8 |
|---|---|---|
| `mockup.html` | `cc7239a37a13a5cd7ea694eb20e8fce9` | `cc7239a3` |
| `mockup.md5` | `277380b87839afed41db3dffa851c19f` | `277380b8` |
| `MERGE.md` | `a657b07400344d5bd8d78591e84efd16` | `a657b074` |
| `flow.md` | `b85e8bbeb99b38ba9cea3bd2e64aff3a` | `b85e8bbe` |
| `spec.md` | `c2e714fb99e55e59439e2c279acbf5b9` | `c2e714fb` |
| `README.md` | `803f081800b69c3eec7421126e8022cf` | `803f0818` |

shots 24 帧 + `mockup-full.png` 已核在 `final/shots/`（本 tech 不逐张抄 md5；实现对照 mockup 与 shots，不以过程稿为准）。

---

## 0. 现状事实（相对 tip `cfe831a53f7d` / cut / final · 无完整 clone）

1. **无 kind `GET /upload` 仍是「照片放到哪里？」整页三选。** `pages.upload_page`：无 `kind` 时渲染 `upload.html` 的 `#photo-entry`（孩子 / 本人 / 旧库导入）。文案键 `photo_copy.entry_title` =「照片放到哪里？」。这就是本刀要拆掉的表面。有 `kind=child|self` 时 302 到来源（时光线或本人相册），确认整屏已在 `.2` 拆掉。
2. **FAB 选照片已经就地走底签，本刀不改这条。** 时光线 FAB `fab_href` 指向 `/upload?kind=child`（会 302 回来）；菜单里「照片」是 `data-slip-pick`，选完就地滑出已上线底签。家人相册空态 CTA 仍是实心钮「放入第一张」+ `data-slip-pick`（本人路径，不是无 kind 深链）。
3. **空态时光线 CTA 仍是实心主钮「放入第一张」。** `stage_inner.html`：`uds.empty_tl` =「还没有照片」，`uds.empty_cta` =「放入第一张」，`<button class="btn" data-slip-pick>`。现网断言 `href="/upload"` 不出现在空态（`test_empty_state_picks_in_place`）。final 要把这里改成浅色文字链「去上传」，去向是无 kind `/upload` 的就地选图卡，不再就地弹出底签、也不进旧三选页。
4. **`mode=edit` 仍走** `GET /photos/{photo_id}/edit` → `_upload_form(..., kind="child", mode="edit")` + `upload.html` 编辑分支 + `photo.js`，标题「改这张照片」、主钮「保存修改」。本刀不改。
5. **写接口、默认值、进度与成功去向已是 `.2` 口径，本刀不改。** 底签默认今天 + `localStorage` 键 `fa.last_tagged_child.{user_id}`；今天主钮「放入」、非今日「放进这一天」；禁止自动传；独孩芯片只写「今天」；点主钮后同一条换成齐格签进度；串行 `POST /api/photos`；成功落到 `/children/{id}/days/{taken_on}` 或本人相册当天齐格。体积帽 / 格式 / 拒绝码沿用 `.2`。
6. **相邻入口现网位置。** 「记在我名下」在家人 tab / 本人相册（青绿句可见），不在无 kind 三选里才算「另有入口」——三选拆掉后必须仍只走家人入口。旧库导入在设置 `/legacy-import`，文案已是「导入旧照片」/「从本机一次选以前的照片」；设置页现网不是 mockup 里「访问地址 / 导入旧照片 / 家里的人」三行并排（访问地址在长辈入口区，成员入口是按钮），本刀不重排设置页。只读打开 `/upload` 已是 403，现网页「没有访问权限」，不露家庭名；非成员拒绝码无 `code`。
7. **线上** systemd `family-album.service` + 单进程 uvicorn `:8000`，STORAGE=local。微信仍停。

---

## 1. 范围 / 非范围（严格按 cut「窄」）

### 1.1 In（P0 ↔ cut E1–E8 / AC-E1…E9）

| In | 内容 |
|---|---|
| E1 取消三选整页 | admin/uploader 打开无 kind `/upload`（含空态「去上传」、书签、刷新），不再出现「照片放到哪里？」整页三选（孩子 / 本人 / 旧库导入）。页面上不得再渲染 `#photo-entry` / `entry_title`。 |
| E2 同一条底签 | 还没有图：当前屏就地选图卡（「选几张放进来」/「从本机选照片，一次可以选多张。」/「从相册选」），选完就地滑出与 FAB **同一条已上线底签**。已经有图：页上是**本批预览格**（不把本批叠在时光线旧照片下面），立刻出底签；只靠底签已有的小缩略和「n张」说明，**不加**新提示条 / 横幅 / 额外一行（不写「这些图还在，马上就能放」）。默认今天、上次挂的孩子。主钮今天「放入」、非今日「放进这一天」。禁止自动传。底签形态与独孩口径继承 `.2`。 |
| E3 不重选 | 同一次页面会话里，深链已带图（已选本批）时，不清空、不 302 / 不打发回时光线再选一遍。改日期、改挂谁、轻叉前都留在 `/upload`。完整刷新后的文件约束见 §6.4 / §10.1。 |
| E4 入口边界 | 「记在我名下」仍只走家人入口；旧库导入仍只走设置既有入口；无 kind 深链不提供这两项。`mode=edit` 行为与 `.2` 相同。空态「去上传」不再掉进旧三选页。 |
| E5 继承 `.2` | 点「放入」后同一条换成齐格签进度；成功落到当天齐格，能看见刚传的图；天页齐格、时光线小排、再贴人规则保持 `.2`，不回退、不重做。 |
| E6 双端 | 390 与 ≥1024 同路径、同规则、同文案。手机贴在当前页脚，桌面停在内容柱（max 720）脚。地址栏可见 `/upload`。 |
| E7 权限 | 只读无入口（无 FAB、无「去上传」、无底签）；打开 `/upload` 为 403，可看本家天页与时光线（同 `.2`）。非成员拒绝码与现网一致，不露家庭名。「上次挂的孩子」只作本账号偏好，不外传。 |
| E8 COPY | 禁「会员\|开通\|商城\|发现\|广告」；文案口语；不写压缩率 / KB / 画质。 |

### 1.2 Out（硬闸，不做）

再贴人芯片改版；把人踢回时光线重选；改 `.2` 已上线的底签形态 / 进度签 / 天页齐格 / 时光线小排；改日堆签 / 看谁签；把旧库导入或「记在我名下」塞回这条无 kind 深链；改 `mode=edit`；改权限三档；新表新列；日志 / 一日一句 / 视频上传主路径；画质设置；整站改版；评论；微信小程序；商城 / 发现 / 广告 / 会员。

**明确放弃（final MERGE，不作）：** 选图笺（时光线脚撕边条：「把照片放这里」/「选几张放到今天」/「会挂在果果名下」/「选照片」）；底签加载句「这些图还在，马上就能放」及静息底签上任何新提示条 / 横幅 / 额外一行；把本批已选图叠在时光线旧照片下面；把空态「去上传」做成实心主钮、与 FAB 抢主行动；以过程稿「就地底签」为主形态。

不能把本刀折进已发布的 `.1` / `.2` tag。今晚不上。

---

## 2. 与 final「就地签」对照

工程只认本目录 final（已核）`cc7239a3…`。主形态取**就地签**：本刀做的是**入口**，不是新的一条决策条。打开无 kind `/upload`，当前屏留着，已上线底签就地滑出。不以过程稿或脚下签为准。

### 2.1 24 帧清单（实现对照）

`tl` · `go_upload` · `kindless_empty` · `bar` · `kindless_kept` · `kindless_refresh` · `bar_day` · `solo` · `self_entry` · `settings_legacy` · `progress` · `fail_one` · `err_size` · `done_day` · `ro_upload` · `err_perm` · `edit_mode` · `desk_kindless_empty` · `desk_kindless_kept` · `desk_bar` · `desk_progress` · `desk_day` · `desk_self_entry` · `desk_settings_legacy`

AC 对照见 flow 状态机。`tl` / `bar` / `bar_day` / `solo` / `progress` / `fail_one` / `err_size` / `done_day` 的**条与格**原样继承已核 `.2` 底签与齐格签，本刀不重画这些样式。`self_entry` / `settings_legacy` / `edit_mode` 是入口边界对照帧：证明这三项不在无 kind 深链上，且行为与 `.2` 相同，不借本刀改版。`ro_upload` / `err_perm` 的拒绝码与 403 页继承 `.2`（见 §10.1，不改 403 模板文案）。

不挡核（不必改 mockup / 不改 md5）：产品页没有 mockup 里的演示地址栏壳，真实地址就是 `/upload`；`kindless_refresh` 完整刷新后的 FileList 约束按 §6.4 验收，不把「刷新后字节仍在」写成 AC。

---

## 3. 数据模型与迁移（SQLite · **本刀零新表零新列**）

### 3.1 策略（零新表 · 可回退）

不改 `photos` / `photo_children` / 成员 / 孩子表，不新增列，不改索引。本刀只改无 kind `/upload` 的页面表面、空态 CTA，以及让已上线底签在该页就地工作。「上次挂的孩子」继续只用现网 `localStorage` 键 `fa.last_tagged_child.{user_id}`，不入库、不外传。

### 3.2 体积 / 压缩常量（写死，沿用 `.2`，本刀不改）

原文件确认步 20 MiB；单批客户端 50 张；客户端默默压缩，失败则原件 ≤帽则回退原件。产品 UI 不写 KB/MB、压缩率、画质。服务端托底「20MB」不在底签 / 进度 / 失败签 / 就地选图卡上原样露出。

### 3.3 迁移步骤

`scripts/migrate.py` 与 `app/migrate_schema.py` 增加一条 **noop** 打印：`upload_kindless_slim ready (no new tables)`。必须**保留**已有 `upload_day_slim ready (no new tables)` 与 `upload_confirm_slim ready (no new tables)`，不得删掉。migrate 不改 `sqlite_master`，不碰媒体目录。测试在隔离临时目录跑，不指向生产 `app.db` / `MEDIA_ROOT`。

### 3.4 回退到 `v2026.11.14.2`

checkout 已发布 tag `v2026.11.14.2`（对象仍指向 `cfe831a53f7d`，本刀不得改写该历史 tag）→ 跑 migrate（noop）→ 重启服务。禁 DROP，禁 `docker compose down -v`。媒体与库文件保留。回退后无 kind `/upload` 会重新出现「照片放到哪里？」整页，空态 CTA 回到「放入第一张」就地选图；这是预期回退面，不是事故。

### 3.5 删除 / 回退保留什么

不删用户照片、不删孩子 / 成员、不改密钥、不改 `.env`。本刀若有新静态样式，回退随 tag 一起回到 `.2` 文件即可。

---

## 4. 权限与隐私

三档不变：

| 角色 | 无 kind `/upload` / 底签放入 | 看某一天页 / 时光线 | 删照片 |
|---|---|---|---|
| admin | 可 | 本家 | 全部 |
| uploader | 可 | 本家 | 仅自己的 |
| 只读长辈 | 不可（无入口；403） | 本家 | 不可 |
| 非成员 / 移出 | 不可（拒绝码与现网一致） | — | — |

- 仅本家庭；无公开链、无推送外发。
- 「上次挂的孩子」只作为本账号偏好，不外传、不改权限三档。
- 只读：无 FAB、无「去上传」、无底签、无就地选图卡；`GET /upload`（无论有无 kind）403；可看本家天页与时光线。403 页不露家庭名。
- 非成员 / 移出：403 **无 code**（JSON 无业务码）；页不露家庭名、不露孩子名。拒绝码与现网一致，本刀不改 403 模板。
- 写接口仍走 `POST /api/photos`，只读 403 `photo_forbidden_role`，非成员 403 无 code，与 `.2` 相同。

---

## 5. 接口 / 路由

### 5.1 API 形态选定（写死）

不新增业务 API。日常放入仍是已上线的 `POST /api/photos`（multipart，字段与帽与 `.2` 相同）。不把文件经 URL 传递，不新增上传会话表。

### 5.2 页面路径

| 路径 | 本刀行为 |
|---|---|
| `GET /upload`（无 kind） | admin/uploader **200**。不再渲染三选整页。无图：就地选图卡；有图（同一次会话已选）：本批预览格 + 立刻出 `.2` 底签。模板须 `include` 已上线 `fragments/upload_slip.html`（`slip_kind=child`），复用 `upload_day_slim.js`。地址就是 `/upload`。 |
| `GET /upload?kind=child` / `?kind=self`（可带 `child=`） | **保持 `.2`**：302 到来源。不把人留在填表整屏，也不改成新的无 kind 页。 |
| `GET /photos/{id}/edit` | **不改**。仍是「改这张照片 / 保存修改」。 |
| 时光线空态「去上传」 | 浅色 accent 文字链，指向无 kind `GET /upload`。不再是实心 `.btn`，不再 `data-slip-pick`。只读空态仍无此链。 |
| 时光线 / 家人相册 FAB 选照片 | **不改**：仍就地 `data-slip-pick` + 已上线底签。 |
| 本人相册空态「放入第一张」 | **不改**（家人路径，不是本刀无 kind 入口）。 |
| 设置 `/legacy-import`、家人「记在我名下」 | **不改路径、不塞回无 kind 页**。设置页不重排。 |
| 凡现网已指向无 kind `/upload` 的链接（含家人列表页 `fab_href=/upload`） | 一律落到就地选图卡 / 本批预览格，不再进三选。默认按孩子底签（今天 + 上次挂的孩子），不是「记在我名下」。 |

`POST /upload` 编辑提交若现网仍被 `mode=edit` 使用，不得拆掉。无 kind 日常放入不走这条 POST，走 `POST /api/photos`。

### 5.3 校验与拒绝码（沿用，不新造业务码）

只读页 / 写 403、非成员 403 无 code、缺孩 `child_required`、超帽 / 非图 / 50 张批次，全部沿用 `.2`。不新造业务码。

### 5.4 成功去向（写死，沿用 `.2`）

点「放入」成功后，从无 kind `/upload` 同样落到当天齐格（`/children/{id}/days/{taken_on}?new=` + toast「已放进今天 / 已放进这一天」+ 新图细描边），不留在空白选图卡当完成态，不回空白首页。家人路径成功去向仍只属于家人入口，本刀无 kind 深链不提供「记在我名下」。

---

## 6. 前端交互要点（1:1 final）

### 6.1 状态机（对齐 flow）

```
空态时光线 #s=go_upload
  └─ 「去上传」（浅色文字链）→ 无 kind /upload 就地选图     #s=kindless_empty
        └─ 选完 → 本批预览格 + .2 底签（停在 /upload）     #s=bar
深链已带图（同一次会话）                                    #s=kindless_kept
  └─ 页上本批预览格 + 立刻出底签，不踢回重选
        ├─ 轻叉 → 放弃，不上传，回到就地选图卡（仍在 /upload）
        ├─ 非今日 → 主钮「放进这一天」                     #s=bar_day
        ├─ 独孩：芯片只写「今天」，无改挂谁                #s=solo
        └─ 点「放入」→ 同一条换成齐格签                    #s=progress
              ├─ 一张失败其余已入                          #s=fail_one
              ├─ 超帽拦在底签                              #s=err_size
              └─ 完成 → 当天齐格                           #s=done_day
刷新 / 书签打开无 kind /upload                              #s=kindless_refresh
  └─ 不得再出三选整页；完整刷新后若 FileList 已空，回到选图卡（§6.4）
家人入口 / 设置旧库 / mode=edit / 只读 403 / 非成员 403：对照，不改
桌面同路径：desk_kindless_empty / desk_kindless_kept / desk_bar / desk_progress / desk_day
```

### 6.2 无 kind 页表面（本刀主改）

- **就地选图卡**（无图）：结构对齐 mockup `pickCard`——插图、标题「选几张放进来」、说明「从本机选照片，一次可以选多张。」、次按钮「从相册选」（`class` 用 ghost / 次级，不是实心主行动与底签「放入」抢）。隐藏 `input[type=file]`，`accept` 与 `.2` 相同，可多选。点「从相册选」只打开系统相册，**不自动传**。
- **本批预览格**（已选）：页上只展示本批缩略（mockup `keptGrid`），**不要**在此页渲染时光线旧照片。底签同时有小缩略 +「n张」。不加「这些图还在」之类新行。
- 选完不换页、不 302 回时光线。底签从页脚（桌面在内容柱脚）滑出，形态锁死为已核底签 `6e6a9099`：暖纸 `#FFFCF7`、顶撕边、决策条无齿孔、左轻叉、芯片「今天 · {名}」、主钮口径与 `.2` 完全相同。
- 轻叉：不上传，收起底签，清空本批，回到就地选图卡，仍停在 `/upload`。
- 禁止采用选图笺（时光线脚那条撕边选图条）。

### 6.3 空态「去上传」

`uds.empty_tl` 仍是「还没有照片」。`uds.empty_cta` 改为「去上传」。控件是浅色 accent **文字链**（可用 class 如 `go-upload`），**不是** `class="btn"` 实心主钮，避免与 FAB 抢。`href="/upload"`（无 kind）。去掉空态上的 `data-slip-pick`。只读看不到这条链。有照片的时光线空态以外的 FAB 行为不改。

本人相册空态「放入第一张」保持 `.2`，本刀不动。

### 6.4 刷新 / 书签与「已选还在」（写死）

浏览器不允许在完整文档刷新或新的书签会话里恢复 `File` 对象（没有用户再次点选）。本刀**不做** IndexedDB / 文件系统缓存来持久化原图（超出 cut，且把原图落到本机缓存有隐私风险）。

因此验收口径写死为：

1. **同一次页面会话**（选完后改日期、改挂谁、底签上掀、切前后台但不卸载本页）：本批预览格与底签必须还在；不清空；不踢回时光线重选。这是 cut E3 / AC-E3 的硬约束。
2. **书签、刷新不得再出三选页**（cut E1）是硬约束：`GET /upload` 只允许就地选图卡或已选态，禁止 `#photo-entry` /「照片放到哪里？」。
3. **完整重新加载后若 FileList 已空：** 回到就地选图卡，地址仍是 `/upload`，不踢回时光线，也不提示「这些图还在」。用户需再选一次。不把「刷新后文件字节仍在」列为 AC，也不为此改 mockup md5。`kindless_refresh` 帧按「刷新后仍不是三选页」验收。

### 6.5 默认值、进行中、失败、成功

全部继承 `.2`，不重做样式、不改 JS 内核口径：默认今天 + 上次挂的孩子（无偏好则按 `.2` 已写回退：独孩取其唯一人，多孩未选则主钮不可用直到选了人）；禁止自动传；独孩无改挂谁；点主钮后齐格签进度（细线跟当前张）；单张失败 / 超帽 / 非图 / 取消 / 迟到响应 / 断网与 `.2` 相同。成功落到当天齐格。

### 6.6 双端 / token

不新增色相。沿用 `--bg #FBF7F2`、`--surface #FFFFFF`、`--ink` / `--ink2` / `--ink3`、`--accent #C8553D`、`--family #2F6F6A`、`--paper-edge #E8D9C4`、暖纸 `#FFFCF7`。手机 390、桌面内容柱 max-width 720 居中。新样式只覆盖就地选图卡、本批预览格、空态文字链，不得改底签 / 进度签 / 天页 / 时光线小排的已核规则。

---

## 7. 失败路径表（门禁要求 · 扩表）

| # | 场景 | 服务端 | 前端 | COPY key | 测试名 |
|---|---|---|---|---|---|
| F1 | 无 kind `/upload` 取消三选 | 200 | 无「照片放到哪里？」、无 `#photo-entry`、无三选里的旧库入口 | — | `test_kindless_upload_no_choice_page` |
| F2 | 无图就地选图卡 | 200 | 可见「选几张放进来」「从相册选」；地址 `/upload`；无选图笺 | `pick_title` / `pick_cta` | `test_kindless_empty_shows_pick_card` |
| F3 | 选完就地出底签、不自动传 | 无写 | 本批预览格 + `.2` 底签；选完不发 `POST /api/photos` | `submit` | `test_kindless_select_shows_slip_no_auto` |
| F4 | 已选不踢回时光线 | 无写 | 同一次会话改日期 / 上掀后预览格与文件仍在；无 302 回 `/` | — | `test_kindless_keeps_files_in_session` |
| F5 | 轻叉放弃 | 无写副作用 | 收起底签，回到选图卡，仍在 `/upload`，不上传 | `dismiss` | `test_kindless_dismiss_back_to_pick` |
| F6 | 刷新 / 书签不再出三选 | 200 | 完整刷新后无三选页；FileList 空则回选图卡，不踢回时光线 | — | `test_kindless_refresh_no_choice_page` |
| F7 | 空态「去上传」 | 200 | 浅色文字链「去上传」，`href="/upload"`，不是实心 `.btn`，无 `data-slip-pick`；只读无此链 | `empty_cta` | `test_empty_cta_goes_to_kindless_upload` |
| F8 | 记在我名下不在深链 | 200 | 无 kind 页无「记在我名下」入口；家人 tab 青绿句仍在 | `self_note` | `test_self_entry_not_on_kindless` |
| F9 | 旧库仍走设置 | 200 | 无 kind 页无 `#legacy-import-entry`；设置入口与文案仍在 | `legacy` 现网键 | `test_legacy_stays_on_settings` |
| F10 | `mode=edit` 不改 | 200 | 「改这张照片 / 保存修改」，仍走 `photo.js`，无本刀底签当编辑主表面 | `u_title_edit` | 改写并保留 `test_edit_and_kindless_upload_unchanged` 的 **edit** 段 |
| F11 | 默认今天 + 上次挂的孩子 | — | 不改日期、不改挂谁，点「放入」即今天、挂偏好孩 | `chip_today` / `chip_who` | 沿用 `.2` 默认测并扩到无 kind 页 |
| F12 | 今日「放入」/ 非今日「放进这一天」 | — | 与 `.2` 同一条底签，禁止把今日钮写成「放进这一天」 | `submit` / `submit_other` | 沿用 `.2` COPY 测并扩到无 kind 页 |
| F13 | 禁止自动传 | — | 选完只出底签，直到点主钮才 POST | `submit` | 沿用并扩 |
| F14 | 独孩无改挂谁 | — | 芯片只有「今天」，上掀无小脸 | `chip_today` | 沿用并扩 |
| F15 | 点主钮后齐格签进度 | — | 同一条换成「正在放入 i/n」+ 细线跟当前张 | `progress` | 沿用 `.2` |
| F16 | 轻叉 / 未提交离页 | 无写副作用 | 无脏行 | `dismiss` | 沿用 `.2` |
| F17 | 进度中取消 / 离页 | 已 201 保留 | 不回滚；有成功则「去看已放入的」 | `fail_one` / `fail_go` | 沿用 `.2` |
| F18 | 迟到响应 | 可能已入库 | **忽略** UI 鬼影，不二次跳转 | — | 沿用 `.2` |
| F19 | 只读写 | 页 403；API `photo_forbidden_role` | 无 FAB / 无「去上传」/ 无底签；可看齐格与当天卡；不露家庭名 | 现网 403 / `ro_meta` | 沿用并扩 `GET /upload`（无 kind） |
| F20 | 非成员 / 移出 | 403 **无 code** | 现网 403 页，不露家庭名、不露孩子名 | 现网 403 | 沿用并扩无 kind `/upload` |
| F21 | 404（误打日页/媒体） | 现网 404 | 不泄露家庭名 | — | 既有扩 |
| F22 | 422 / 缺孩 | 现网 `child_required` | 底签未选孩主钮不可用；直调当失败不白屏 | — | 沿用 `.2` |
| F23 | 503 / 存储失败 | 5xx | 本张失败；已成功保留 | `fail_one` | 沿用 `.2` |
| F24 | 断网 | 无完整响应 | 不假装整批成功；已成功保留 | `fail_one` / `fail_go` | 沿用 `.2` |
| F25 | 多进程滚动发布 | 混合 404 无 code / 5xx | **不当永久无功能**；可重试 | — | 沿用 `.2` |
| F26 | 单张原文件 >20 MiB | 通常无请求；直调 400 | 底签 `err_size`；UI 不写 KB/MB | `err_size` | 沿用 `.2` |
| F27 | 非图 / 损坏 | 通常无请求；直调 400 | 跳过该张，其余可提交 | `err_bad` | 沿用 `.2` |
| F28 | 单批 >50 张 | 无请求 | 口语拦 | `err_batch` | 沿用 `.2` |
| F29 | 部分失败汇总 | 各张独立 201/失败 | 不回滚成功 | `fail_one` / `fail_go` | 沿用 `.2` |
| F30 | 成功去向 | — | 落到当天齐格且可见新图；不把选图卡当完成态 | `done_toast` | 沿用并扩无 kind 起点 |
| F31 | CSRF / 会话过期 | 现网 403 | 不假装成功 | — | 既有扩 |
| F32 | 禁词 | — | 会员\|开通\|商城\|发现\|广告 = 0；产品 UI 无压缩率 / KB / 画质 | — | `test_copy_forbidden_words_upload_kindless_slim` |
| F33 | 天页 / 时光线 / 底签形态不回退 | — | 底签仍无决策条齿孔；进度签 / 齐格 / 小排与 `.2` 一致 | — | 沿用 `.2` 形态测，禁止改底签 CSS 内核 |
| F34 | kinded `/upload?kind=*` 仍 302 | 302 到来源 | 不把 kinded URL 当成新的就地选图页 | — | 沿用 `test_upload_kind_redirects_away_from_form` |
| F35 | 不进选图笺、不加加载条 | — | 产品 COPY / DOM 无「把照片放这里」「这些图还在，马上就能放」 | — | `test_kindless_no_pick_ticket_or_loading_line` |
| F36 | `.2` 回归：FAB 仍就地底签 | — | 有照片的时光线 FAB 选完仍就地出底签，不进 `/upload` 填表 | — | 沿用 `.2` `test_confirm_stays_on_page_slip` |

须改写、不得再当绿的旧断言：

- `test_edit_and_kindless_upload_unchanged`：拆开。edit 段保持；kindless 段改为「无三选、有选图卡 / 底签」。
- `test_copy_no_size_or_quality_jargon` 里 `assert "照片放到哪里" in wizard.text` 必须翻转。
- `test_empty_state_picks_in_place`（现断言「放入第一张」+ `data-slip-pick` + 无 `href="/upload"`）改为 F7。
- `test_upload_day_slim` 中仍要求无 kind 页出现「照片放到哪里」的断言一并翻转。

---

## 8. 文案（COPY 1:1 final · 产品键）

在 `app/upload_day_slim_copy.py`（或并列小模块再由 `constants` 导出）增改本刀键。产品文案与 final `spec.md` / mockup `COPY` 对齐；`anno_*`、示例人名/年龄/稿内桌面壳 / 演示地址栏 **不进**产品常量。动态句用占位。

| key | 文案 / 规则 |
|---|---|
| `empty_tl` | 还没有照片（沿用） |
| `empty_cta` | **去上传**（本刀改值；浅色文字链，不是实心主钮） |
| `pick_title` | 选几张放进来 |
| `pick_sub` | 从本机选照片，一次可以选多张。 |
| `pick_cta` | 从相册选 |
| `submit` / `submit_other` | 放入 / 放进这一天（沿用 `.2`，今日钮禁止写成「放进这一天」） |
| `self_note` | 记在我名下，不进孩子时光线（仍只在家人入口，无 kind 页不出现此入口） |
| `legacy` 现网 | 导入旧照片 / 从本机一次选以前的照片（设置页，不改值） |
| 编辑现网 | 改这张照片 / 保存修改（不改） |
| 底签其余键 | 全部沿用 `.2`（`n_zhang`、`chip_*`、`progress`、`fail_*`、`done_toast*`、`err_size` / `err_bad` / `err_batch`、`dismiss`…） |

**必须从无 kind 页消失的产品文案：**「照片放到哪里？」、`entry_child` / `entry_self` 三选标题、「选好后再选图…」这类旧整页说明。这些键可留在 `photo_copy` 以免误伤无关导入，但 `GET /upload`（无 kind）和空态「去上传」路径不得再渲染它们。

沿用、本刀不重写：看谁签、既有 403 页句（「没有访问权限」等，见 §10.1）。产品 UI **不出现**「会员」「开通」「商城」「发现」「广告」，也不把压缩率、体积字母缩写（KB/MB）、画质写进产品文案。设计标注（蓝虚线、「本刀取消」）不进入产品 COPY。

---

## 9. 验收（AC）与测试计划

### 9.0 门禁测试纪律

测试一开始就隔离临时目录（独立 `app.db` 与 `MEDIA_ROOT`），不指向生产或共享脏数据。交实现后审前，在 CloudAgent / 共享环境把**全量** pytest **连跑 3 次**，并按历史 QA / review 问题自查（今日钮 COPY、禁词、只读无入口、不露家庭名、底签无决策条齿孔、`mode=edit` 不误伤、kinded 302 仍在、零新表）。三次次数与结果写入 PR 说明。现网常无 GitHub Checks，以这 3 次为准。

### 9.1 AC ↔ 测试

| AC | 怎么验 |
|---|---|
| AC-E1 简洁 | F1–F3、F35：无三选整页；决策走同一条 `.2` 底签 |
| AC-E2 默认 | F11–F12：不改日期、不改挂谁即今天 + 上次的孩子；不再做三选 |
| AC-E3 一看就会 | F3–F6：已选图在会话内还在；主钮明确；禁止自动传；刷新不是三选页 |
| AC-E4 入口 | F7–F10：去上传文字链；记在我名下走家人；旧库走设置；edit 不改 |
| AC-E5 继承 | F15–F18、F26–F30、F33、F36：进度签 / 当天齐格 / 天页 / 时光线 / 底签形态 / 再贴人与 `.2` 一致 |
| AC-E6 双端 | 390 与 ≥1024 各走通无 kind 深链 → 底签放入（实现用 CSS / 布局断言；设计/QA 各记一条走查） |
| AC-E7 权限 | F19–F20：只读无入口；非成员拒绝码与现网一致，不露家庭名 |
| AC-E8 COPY | F32：无「会员\|商城\|发现\|广告」 |
| AC-E9 发布 | tag `v2026.11.15.1` + 发布日志 + 可回退 `v2026.11.14.2`（禁 DROP / `down -v`）。**今晚不执行本条**；实现只交 PR，merge/tag/部署另等次日令 |

四角色（管理员 / 可上传 / 只读长辈 / 非成员）走查，记体验卡点。新测主文件建议 `tests/test_upload_kindless_slim.py`；旧测按 §7 末条改写，不要留「无 kind 仍是三选」的绿断言。

### 9.2 四角色机器人走查

admin / uploader：空态「去上传」→ 选图卡 → 底签放入 → 当天齐格可见新图；已选会话不丢图；家人入口与设置旧库仍在原处。只读：无入口，403，可看本家。非成员：403，不露家庭名。

### 9.3 验收节（写死）

本刀上线后（**不是今晚**）QA / QA2 补生产核验节（建议 AC-E9 / 生产 OpenAPI `2026.11.15.1`）。实现 PR 说明须写明回退 tag、禁 DROP、未改密钥、微信仍停。

---

## 10. 待决问题

### 10.1 已关（cut / final / 本刀拍板）

1. **主形态是就地签，做入口、不改条。** 工程只认 final mockup `cc7239a3`。底签形态锁 `6e6a9099`，齐格锁 `6f2a5be0`。过程稿与脚下签不作认据。
2. **取消「照片放到哪里？」整页三选**，不得改成另一种填表整页。
3. **已带图用本批预览格**，不把本批叠在时光线旧照片下面。
4. **不采用选图笺**；**不加**「这些图还在，马上就能放」或底签新提示条。
5. **空态「去上传」是浅色文字链**，不是实心主钮；去向无 kind `/upload`，不再 `data-slip-pick`。本人相册「放入第一张」不改。
6. **记在我名下只走家人入口；旧库只走设置；`mode=edit` 不改。**
7. **再贴人芯片 / 多选规则 / 芯片文案 / 多人落地**保持 `.2`，本刀不改。
8. **权限三档不变；零新表零新列。** 「上次挂的孩子」只作本账号本地偏好。
9. **刷新 / 书签与 FileList（关闭，见 §6.4）。** 同一次会话必须还在；完整刷新不得再出三选，FileList 空则回选图卡。不做 IndexedDB 缓存原图。不把「刷新后字节仍在」列为 AC。
10. **设置页不重排（关闭）。** mockup `settings_legacy` 用三行并排证明「旧库入口仍在、不在深链上」。现网设置不是这三行并排。本刀不重排设置；保留 `/legacy-import` 与现网文案。若要收成三行并排，另开刀。
11. **403 文案继承 `.2`（关闭）。** final 对照帧写「你没有权限做这件事 / 家里的内容不会在这里显示」。现网只读 / 非成员页是「没有访问权限」，测试已按此锁。cut 写拒绝码与现网一致、同 `.2`。本刀**不改** `403.html`，避免波及非本刀路径。对照帧视为示意。不露家庭名仍是硬约束。
12. **今晚不上。** 目标 tag 仍是 `v2026.11.15.1`，回退 `.2`。本刀实现只交 PR。
13. **微信仍停**；硬闸无商城 / 发现 / 广告 / 会员；全路径不出现「会员」二字。
14. **不能折进已发布的 `.1` / `.2` tag**；不得改写已上线历史 tag。

### 10.2 仍开

无。§10 已全关，可交实现前审。未过 tech-r1 前禁止开 CloudAgent。

---

## 11. 发布与回退

- 版本字符串：FastAPI / OpenAPI `2026.11.15.1`。
- 文档：`CHANGELOG` + `releases/v2026.11.15.1.md`（与 tech 同步；写清回退 `v2026.11.14.2`、零新表、今晚先不上线）。
- 仓库动作（**仅 tech-r1 通过后**）：CloudAgent 基线 `cfe831a53f7d`，**只开 PR，不合、不打 tag、不发 Release、不部署**。今晚不上。
- 次日若终审通过再：两父提交 merge（不 squash / 不 rebase / 不 force push）→ annotated tag `v2026.11.15.1` → 非 draft Release → 请 srv 按现有 systemd 路径部署，保留媒体和库，禁 DROP / `down -v`，回退 `v2026.11.14.2`。未独立核到生产前不上线结案。
- 回退：checkout `v2026.11.14.2` @ `cfe831a53f7d`，migrate noop，重启。不改该历史 tag。

---

## 12. Unlock（实现顺序；仅门禁通过后）

1. 拆掉无 kind `/upload` 三选整页；落地就地选图卡 + 本批预览格；`include` 已上线底签，复用 `upload_day_slim.js`（不改底签形态内核）。
2. 空态「去上传」改为浅色文字链，指向 `/upload`；去掉该处 `data-slip-pick`。
3. 接上选图 → 预览格 + 底签、轻叉回选图卡、禁止自动传、成功落到当天齐格；会话内不丢图。
4. 改写旧测（§7 末条）；新增 `tests/test_upload_kindless_slim.py`；migrate noop 行；version；CHANGELOG + releases。
5. 隔离临时目录，全量 pytest ×3；回归 FAB 就地底签、kinded 302、edit、设置旧库、家人入口、只读 / 非成员 403、看谁签 / 视频 / 天页 / 时光线。
6. 只交 PR。今晚不 merge / tag / 部署。

**再次强调：** §10 已全关；未过实现前审前，**禁止**开 CloudAgent；本任务只落盘 tech，不改业务代码、不 force push、不 clone GitHub、不开 PR、不开 CloudAgent。不动已上线 `.2` / 已发 tag。今晚不上。微信仍停。

---

## 实现门禁表（汇总）

| 门禁 | 状态 |
|---|---|
| final mockup md5 已锁 `cc7239a3` | 已锁（就地签，已核 / PASS） |
| 底签形态仍锁 `6e6a9099`，本刀不改 | 已锁 |
| cut 锁体 `7a7542a3` + 全文件 `16bc0288` | 已锁；PM 拍板已在 cut |
| 失败路径表（取消 / 迟到 / 403 / 404 / 422 / 503 / 断网 / 滚动发布 + 本刀入口） | 已写 §7 |
| 测试隔离临时目录 + 交审前全量 pytest ×3 | 已写入 §9.0 |
| §10 全部关闭 | 已关（14 条），无仍开项 |
| 零新表零新列；禁 DROP / `down -v` | 已写 |
| 今晚不上；未过 tech-r1 不开 CloudAgent | 已写 |
| 微信仍停；禁词硬闸 | 已写 |
