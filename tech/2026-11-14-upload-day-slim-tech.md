# Tech · 前端精简（窄）齐格签 · v2026.11.14.1

**状态：** 实现前审稿（本刀只落盘 tech，**不开 CloudAgent**、**不开 PR**、**不 clone**）。  
**版本：** `v2026.11.14.1`  
**tech 实测全量 md5：** 以本文件磁盘 `md5sum` 为准（见同目录 `2026-11-14-upload-day-slim-tech.md5`）。
**基线 main tip：`687a063a` = tag `v2026.10.25.9`（EXIF 自动拆日已上，线上已部署）**（监工给定；box 无完整 clone，事实以 tip 语义 + cut/final + GitHub 读 tip 文件为准；**实现时对照 main tip `687a063af141a22443d05f19e0ef263aa4f817cc`** 的 `app/static/photo.js` / `app.css` / `templates/upload.html` / `day.html` / `fragments/stage_inner.html` / `routers/photos.py` / `services/photos.py` · `journals.py`（`build_feed` / `build_family_feed`） / `config.max_upload_bytes` / `constants` / `photo_copy.py`）。  
**回退目标：`v2026.10.25.9`**（tip `687a063a`；本刀**零新表零新列**；回退**不 DROP**；**禁** `docker compose down -v`）。  
**cut：** `/workspace/pm/family_album/2026-11-14-前端精简-cut.md`（**定稿** 2026-11-14 11:03 Asia/Shanghai；PM + PM2 已拍；11:30 文末追加「## 设计 final」；PM2 11:30 可交工程；PM 11:34 再确认两份哈希）。  
**cut 身份锁（锁体，认据 · In/AC）：** `868647b250a086331473737fdb16f0e6`（短 `868647b2`；11:03 定稿正文，**不含**「## 设计 final」；sidecar / 监工 / PM 交工认这份）。  
**cut 全文件（含「## 设计 final」指针）：** `036cc191936390a58dfb8167c200f9d2`（短 `036cc191`；11:29 落盘；In/AC 相对锁体未改）。引用 cut 时**两份哈希一起写**。  
**final：** `/workspace/design/family_album/upload-day-slim/final/` · mockup 实测全量 md5 **`6f2a5be00e2c35a9f1816c3a20ce283f`**（短 `6f2a5be0`；合稿名 **「齐格签」**；designer 11:29 **已核 PASS**；PM2 11:30 可交工程）· 过程稿 `v-designer/` / `v-designer2/` **不作工程认据**。  
结构模板：`2026-10-25-exif-day-split-tech.md`（文首门禁表、失败路径扩表、§10 全关）。邻近参考：`2026-10-25-photo-multi-child-tech.md`（胶片贴 / 日常 `POST /api/photos`）· `2026-10-25-timeline-child-filter-tech.md`（看谁签不改）。

**final 全量实测 md5（落盘前）：**

| 文件 | 全量 md5 | 短 8 |
|---|---|---|
| `mockup.html` | `6f2a5be00e2c35a9f1816c3a20ce283f` | `6f2a5be0` |
| `mockup.md5` | `22791406e219ff33a07e2b874c5eb57c` | `22791406` |
| `MERGE.md` | `1aa571a8487553fe2903204d622a87d8` | `1aa571a8` |
| `flow.md` | `d369db8c20528a32ff9d1afec6c9712f` | `d369db8c` |
| `spec.md` | `2427503af792dc23ac567e6f290544c4` | `2427503a` |
| `README.md` | `a9a2452bf9761bcb4d43d380d6af0735` | `a9a2452b` |

shots 24 帧 + `mockup-full.png` 已核在 `final/shots/`（本 tech 不逐张抄 md5；实现对照 mockup 与 shots，不以过程稿为准）。

---

## 0. 现状事实（相对 tip `687a063a` / cut / final · 无完整 clone）

1. **日常上传是单张页，不是本刀确认步。** `/upload` 无 `kind` 时先出「照片放到哪里？」整页（孩子 / 本人 / 旧库导入）；选 kind 后 `upload.html` + `photo.js` 一次只持有 `state.file` 一张，预览区点选，提交 `POST /api/photos`（`Form`: `kind` / `child_id` / `child_ids` / `taken_on` / `file`）。进度只改按钮文案为「贴着…」，**无** `xhr.upload.onprogress`，**无**默默压缩。成功去向：孩子回时光线 `/?child=&toast=photo_posted`；本人回 `/people/{uid}/album?toast=album`。与 cut「完成后看见刚传的图（当天页）」不符。  
2. **单张帽现网 20 MiB。** `Settings.max_upload_bytes = 20 * 1024 * 1024`；`_read_limited` / `create_photo` 超帽 400，文案「单张照片不能超过 20MB」。`sniff_image` 只接受 jpeg / png / webp（`accept` 同此）。只读写 403 `photo_forbidden_role` + `FORBIDDEN`（「没有访问权限」）；非成员走现网 403 页。  
3. **某一天页已是密缝 `grid3`（3 列、gap 3px），不是邮票齐格。** `day.html` 全部照片进同一套方格并链到 `/photos/{id}`，**无**页内大图左右滑、**无**「共 N 张」、**无**新图细描边。1 / 2 张并未走 4:3 / 对半（天页已是格），但间距过密、无齿孔、日期与孩子名挤在顶栏、无加载骨架格。  
4. **时光线当天把照片铺开。** `stage_inner.html` 用 `.pics.photo-stack` 把当天**全部** `photo_card` 竖着排开。CSS 仍留着 `.pics.n1` 4:3 / `.pics.n2` 对半 / `.pics.n3` 左大右二 + `.ph .more`（+N）规则，主形态虽是 stack，仍属「铺开全部」且与 cut 要取消的藏图形态同源。看谁签在顶，本刀不改。`amber-thumbs` 属往年今日卡，本刀不改。  
5. **家人相册无「当天」页。** `/people/{uid}/album` 把本人 `kind=self` 照片整本平铺。`build_family_feed` 走 `family_child_photos`（实现时再核对），不能假定 self 照片会出现在 `/days/{taken_on}`。成功「落到个人相册对应那天」需要一条按日齐格，**不新表**。  
6. **编辑照片**（`mode=edit`）与 **旧库导入 / 日堆签**、**看谁签**、日志 / 一日一句 / 视频主路径：本刀不改行为。微信仍停。线上现为 systemd `family-album.service` + 单进程 uvicorn `:8000`，STORAGE=local。

---

## 1. 范围 / 非范围（严格按 cut「窄」）

### 1.1 In（P0 ↔ cut E1–E10 / AC-E1…E11）

| In | 内容 |
|---|---|
| E1 入口 | admin/uploader 日常上传（含 FAB 选图后确认步）按本刀收口；只读无入口、无 FAB。 |
| E2 步骤 | 选图 → 确认（挂谁、哪天）→ 完成；≤3；无第二向导；无画质/压缩设置页。 |
| E3 一行状态 | 进行中只占一行暖纸进度签：「正在放入 i/n」+ 底边细线跟**当前这张**（不是整批 i/n 比例）；不写压缩率/KB、不加第二块面板、不只转圈。 |
| E4 默默压缩 | 客户端压到可传体积；界面无「画质 / 压缩」选项或开关。单张帽沿用 20 MiB。 |
| E5 成功去向 | 完成后落到该日天页齐格，能看见刚放入的图：「已放进今天」+ 新图 2px accent 细描边；不盖「刚放进」角标；不丢未知页。家人路径落到本人相册当天齐格。 |
| E6 天页网格 | 整齐邮票齐格：手机 3 列、单格边长 ≥108、间距 10；桌面内容柱内 5 列、间距 12。1 / 2 / ≥3 **同一套**。点开页内大图可左右滑同天其余。顶栏日期只出现一次，并带「共 N 张」。加载用同格骨架。 |
| E7 取消 +N | 取消「1 张 4:3 / 2 张对半 / ≥3 左大右二、溢出资 +N」作为主形态；当天图铺开，多了可下滑，不靠 +N 藏。 |
| E8 时光线当天卡 | 沿用现网日卡信息结构（日期 + 周几 + 年龄胶囊 + 署名）；照片区最多一小排缩略（约 4）+「共 N 张」；点整卡进某一天页；不铺开全部；不用 +N 遮一枚缩略；**不另起日历票根**；宽屏同样一小排。 |
| E9 双端 | 390 与 ≥1024 同路径、同规则、同文案。桌面内容柱 max-width 720 居中。 |
| E10 COPY | 禁「会员\|开通\|商城\|发现\|广告」；文案口语；产品 UI 不出现压缩率、体积字母缩写、画质。 |

### 1.2 Out（硬闸，不做）

评论/回复/@/表情、整站改版、改日堆签/旧库导入确认、改看谁签、画质设置页或压缩开关、改权限三档、新表新列、视频上传主路径、逐张高级编辑器、细密瀑布流、时光线当天卡铺开全部、时光线日历票根、新图「刚放进」角标、进度右侧短棒/第二块面板、按整批比例填细线、改往年今日琥珀卡、改日志/一日一句主路径、微信小程序、商城/发现/广告/会员。不回头改 PR#18。

---

## 2. 与 final「齐格签」对照

合稿主张见 MERGE：底边细线只跟**当前这张**；时光线不另起票根；成功用「已放进今天」+ 细描边。`ro_day` 顶栏按**普通天页顶栏 + 一枚只读签**实现（mockup 顶栏「只读」写了两遍、未带「共 N 张」——不挡核，不按错误处临摹）。`day_many` 末排裁切=可下滑，不是藏图。`day_load` 顶栏「共 N 张」可当元数据先到，骨架只替格子。

### 2.1 24 帧清单（实现对照）

`tl` · `tl_empty` · `confirm` · `progress` · `fail_one` · `err_size` · `err_bad` · `done_day` · `self` · `self_progress` · `self_done` · `day_1` · `day_2` · `day_grid` · `day_many` · `day_load` · `lightbox` · `ro_tl` · `ro_day` · `err_perm` · `desk_confirm` · `desk_progress` · `desk_day` · `desk_tl`

`anno_*` 不进产品页。

---

## 3. 数据模型与迁移（SQLite · **本刀零新表零新列**）

### 3.1 策略（零新表 · 可回退）

复用现网 `photos` + `photo_children`。不新增表、列、索引。批进度、压缩、新图描边均在客户端编排（描边靠落地 URL 查询参数，不入库）。迁移只加一行 ready 标记，无 DDL。

### 3.2 体积 / 压缩常量（写死）

| 常量 | 值 | 用途 |
|---|---|---|
| `MAX_UPLOAD_BYTES` | `20 * 1024 * 1024`（沿用 `settings.max_upload_bytes`，自 `.7`） | 确认步原文件帽；服务端 `_read_limited` 托底。超帽走 `err_size`「先拿掉再放」。 |
| `SILENT_MAX_EDGE` | `2560` | 默默压缩最长边上限（客户端；不展示）。 |
| `SILENT_TARGET_BYTES` | `2 * 1024 * 1024` | 静默目标体积（不展示）。已 ≤ 此值且边 ≤ 2560 则不重编码，避免无谓伤画质。 |
| `SILENT_QUALITY` | 先 `0.82`，仍超目标再 `0.70` | `canvas.toBlob('image/jpeg')`。 |
| `SILENT_FALLBACK_EDGE` | `1920` | 质量降至 0.70 仍超目标时再缩边。 |
| `DAILY_BATCH_MAX_FILES` | `50` | 单批客户端上限（与导入同数量级；界面不写「50」技术口径；超出口语拦）。 |
| `DAILY_CONCURRENCY` | `1` | 串行上传。 |
| 格式 | jpeg / png / webp（与现网 `sniff_image` 一致） | 其它 / 损坏 → 确认步跳过，`err_bad`。 |

**超帽时机（写死）：** 确认步按**原文件** `file.size` 与 20 MiB 比较；超过则 `err_size`，须拿掉再放——与 flow「先拿掉再放」、cut「与现网一致拦」一致。不在确认步偷偷把 >20 MiB 的原件压进来冒充可传。已通过确认的图，提交时再默默压缩（见 §6.3）；压缩后的 payload 仍不得超过 20 MiB（否则该张失败，不假装成功）。

### 3.3 迁移步骤

`scripts/migrate.py` / `migrate_schema.py` 增加 no-op ready：`upload_day_slim ready (no new tables)`。无 DDL，库哈希应不变。

### 3.4 回退到 `v2026.10.25.9`

checkout 该 tag 并按现网路径重启（线上 systemd；compose 环境按既有文档）。**零新表**；已上传照片（含本刀压缩后入库的 JPEG）仍按普通照片可读。**不要** `down -v` / `DROP TABLE` / `DROP COLUMN`。回退后日常上传恢复为单张页、时光线恢复铺开照片卡、天页恢复密缝 `grid3`。

### 3.5 删除 / 回退保留什么

不删用户媒体与 DB。本刀只改模板 / CSS / 日常上传 JS / 文案 / 一条按日相册路由（见 §5）。回退丢的是新 UI 与客户端压缩，不是已入库文件。

---

## 4. 权限与隐私

三档不变。仅本家庭；无公开链、无推送外发。默默压缩只在用户浏览器内为本张可传服务，不外传、不建公开索引、不改权限。

| 角色 | 日常上传 | 看天页齐格 / 时光线当天卡 / 本人相册当天齐格 | 删照片 |
|---|---|---|---|
| admin | 可 | 本家 | 全部（本刀不改） |
| uploader | 可 | 本家 | 仅自己的（本刀不改） |
| 只读长辈 | 不可（无入口、无 FAB；写接口 403 `photo_forbidden_role`） | 本家（同普通照片） | 不可 |
| 非成员 / 移出 | 不可（拒绝码与现网一致，403 无业务 code；不露家庭名） | — | — |

`kind=self` 照片仍不进孩子时光线（现网语义不变）。只读可看齐格与当天卡，无上传按钮。

---

## 5. 接口 / 路由

### 5.1 API 形态选定（写死）

**不新增写接口。** 日常上传仍逐张 `POST /api/photos`（201 + 现网 `photo_payload`）。客户端串行（并发=1）：先默默压缩当前张，再以 `xhr.upload.onprogress` 跟该张字节进度，收到 201 再下一张。不改 patch / 删 / 媒体流。不把 EXIF 分堆 / peek 搬进日常上传。

### 5.2 页面路径

| 路径 | 本刀 |
|---|---|
| `GET /upload?kind=child&child=` · `GET /upload?kind=self` | 确认步收口（多选图 + 日期芯片 + 胶片贴或家人说明 + 底部主钮）。已知 kind 时**不再**先出「照片放到哪里？」整页向导。 |
| `GET /upload`（无 kind） | 仅无上下文深链保留一次入口选择；不是日常 FAB 主路径。 |
| `GET /children/{child_id}/days/{taken_on}` | 孩子当天齐格（改模板/样式；可带 `?new=id,id` + toast）。 |
| `GET /days/{taken_on}` | 全家当天齐格（同模板；仍按现网 feed 内容，不把 self 照片硬塞进孩子时光）。 |
| `GET /people/{user_id}/album/days/{taken_on}` | **本刀新增读页（零新表）**：本人相册当天齐格，模板与天页同一套（顶栏 `self_album`）。家人路径成功去向落这里。原 `/people/{user_id}/album` 整本相册保留，行为不扩范围。 |
| `GET /photos/{id}` | 保留（删 / 保存原图）；大图浏览主路径改为天页lightbox，lightbox 提供入口回到详情。 |
| 旧库导入 / 视频 / 日志 / 看谁签深链 | 不改。 |

### 5.3 校验与拒绝码（沿用，不新造业务码）

| 场景 | 码 / 行为 |
|---|---|
| 只读写 | 403 `photo_forbidden_role` + `FORBIDDEN`；页无入口。 |
| 非成员 / 移出 | 403 **无 code**；现网 403 页，不露家庭名。cut 优先「拒绝码与现网一致」；不改 403.html 另起一套。 |
| 缺文件 / 类型无效 | 400，现网口径（「请上传照片」「只支持 jpeg、png、webp」）。产品 UI 映射为 `err_bad`，不把「20MB」等体积缩写示给用户。 |
| 超 20 MiB | 服务端 400「单张照片不能超过 20MB」（托底不改字符串以免误伤既有测）。产品 UI 只显示 `err_size`。 |
| 缺孩子（kind=child） | 现网 `child_required` / `err_no_child`；确认步未选孩禁用提交。 |
| 会话过期 / CSRF | 现网 403；不假装成功。 |

### 5.4 成功去向（写死）

- 孩子：`/children/{主挂孩子 id}/days/{taken_on}?new={id,id}`，toast「已放进今天」，新 id 细描边。多孩胶片贴时以确认步所选孩子之一（预贴的那位优先，否则所选第一位）作为日页锚点。  
- 家人：`/people/{uploader_id}/album/days/{taken_on}?new={id,id}`，同一套 toast + 描边。  
- 部分失败：留在进度签 `fail_one` +「去看已放入的」→ 已成功张的当天齐格（同样带 `new=`）。无一成功则不跳转，不丢未知页。  
- `new=` 只作本页高亮，不入库；刷新可保留至该次查询，随后导航不再描边。

---

## 6. 前端交互要点（1:1 final）

### 6.1 状态机（对齐 flow）

```
时光线（看谁签不改 + 当天卡一小排 + FAB）
  空态（可传有入口「放入第一张」）
  └─ 选图 → 确认（挂谁、哪天；已选小邮票一行 + 日期芯片 + 胶片贴）
        ├─ 非图 / 损坏 → err_bad（跳过该张，其余可提交）
        ├─ 单张原文件超过 20 MiB → err_size（口语拦，先拿掉再放）
        └─ 提交 → 进度签
              ├─ 单张失败 fail_one（签上可感知 + 去看已放入的）
              └─ 完成 → 当天齐格 + 「已放进今天」+ 新图细描边
家人「记在我名下」：无胶片贴，青绿说明 + 主钮「放到我的相册」，同一套进度签，完成回本人相册当天齐格。
只读：无 FAB，可看当天卡与天页齐格（普通顶栏 + 一枚只读签）。
```

步骤始终 ≤3。主按钮固定底部。手机一屏走完确认提交。日常上传默认今天，可改日期芯片（**不是**日堆签/导入流，可默今天）。

### 6.2 入口收口（砍第二向导）

- 孩子时光线 FAB：先选图（`accept` 多选），进入 `kind=child` 确认步，预贴当前孩子。  
- 家人相册 FAB：先选图，进入 `kind=self` 确认步。  
- 不再把「照片放到哪里？」整页插在 FAB 选图前的主路径上。`mode=edit` 不改。旧库导入入口仍在设置/既有 CTA，不塞进本刀确认步。

### 6.3 默默压缩 + 进度签

- 压缩只在提交后、当前这张上传前，于浏览器内进行（`createImageBitmap({ imageOrientation: 'from-image' })` 或等价；canvas 输出 JPEG）。不上传原文件之外的旁路服务。HEIC 等本就不在 accept 列表；不能解码且格式不在允许集 → 已在确认步 `err_bad`。允许格式但压缩失败：若原文件 ≤20 MiB 则回退传原件，不打断整批。  
- 若压缩结果比原件更大，传原件。  
- 进度签：一行暖纸票（高 40，左小齿孔票根），文案「正在放入 i/n」。**整票底边 3px accent 细线 = 当前这张 `xhr.upload.loaded/total`**（「正在放入 2/5」时不是 40%）。压缩尚未发出请求时细线停在 0，文案仍是「正在放入 i/n」，**禁止**只转圈、禁止第二块面板、禁止右侧短棒、禁止写出压缩率或 KB。  
- 串行：当前张 201 或已记失败后才处理下一张。`visibilitychange` / 离页：中止未完成的 xhr；已 201 保留。迟到 201：忽略 UI 鬼影，不重复跳转。

### 6.4 天页齐格

CSS Grid 等大正方形邮票（虚线齿孔边）。手机 3 列，边距 12、纸内边 10、间距 **10**，单格 ≥108。桌面内容柱（max-width 720）内 **5 列**，间距 12。1、2、≥3 同一套，左起依次放。取消天页/时光线主形态的 4:3、对半、左大右二、+N。多了页面滚动。顶栏：日期+周几只出现一次；副行「共 N 张」及孩子名+年龄（或家人相册名）。不要纸头再重复一天标题。只读：同一套顶栏 + 一枚 `ro_meta` 签。加载：同结构骨架格，不只转圈。日志 / 视频 / 一日一句区块保留在天页，不改主路径，仅照片区换齐格。

### 6.5 大图

点格开页内 lightbox，左右滑同天其余；文案「左右滑，看这一天」+ `i / n`。提供入口进入现网 `/photos/{id}`（删 / 保存原图，权限不改）。不做细密瀑布流。

### 6.6 时光线当天卡

沿用现网日卡结构：日期 + 周几 + 年龄胶囊 + 署名「{who}传」。照片区最多一小排约 4 枚缩略 + 口语「共 N 张」；点整卡进对应某一天页。当天其余照片不在时光线展开。不用 +N 遮缩略。不另起日历票根。桌面同样一小排，不因变宽铺开。当天若还有日志/视频/一日一句，保留现网条目，不把照片再铺成 `photo-stack`。看谁签不改。往年今日琥珀卡不改。

### 6.7 双端 / token

390 与 ≥1024 同路径同文案。Token 对齐 final spec §1（不新增色相）：`--bg` `#FBF7F2`、`--surface` `#FFFFFF`、`--line` `#EDE5DC`、`--ink` / `--ink2` / `--ink3`、`--accent` `#C8553D` / `--accent-soft` `#F8E6DF`、`--family` / `--family-soft`、`--warn` / `--err` / `--ok`、`--paper` / `--paper-line` / `--paper-edge`。进度签 15/800、主钮 15/800、天页顶栏日 17/800、「共 N 张」13/800；圆角：进度签 8、齐格纸/当天卡 14、主钮 12。

---

## 7. 失败路径表（门禁要求 · 扩表）

| # | 场景 | 服务端 | 前端 | COPY key | 测试名 |
|---|---|---|---|---|---|
| F1 | 取消确认 / 未提交离页 | 无写副作用 | 回入口；无脏行 | `cancel`（沿用） | QA |
| F2 | 进度中取消 / 离页 | 已 201 保留；后续不发 | 不回滚；有成功则「去看已放入的」 | `fail_one` / `fail_go` | `test_daily_upload_cancel_keeps_successes` |
| F3 | 迟到响应（已离页后 201） | 可能已入库 | **忽略** UI 鬼影，不二次跳转 | — | `test_daily_upload_late_response_ignored` |
| F4 | 只读写 | 403 `photo_forbidden_role` | 无 FAB / 无上传入口；可看齐格与当天卡 | `ro_meta` | `test_viewer_daily_upload_403` |
| F5 | 非成员 / 移出 | 403 **无 code** | 现网 403 页，不露家庭名 | 现网 403 | `test_outsider_daily_upload_403_no_code` |
| F6 | 404（误打日页/媒体） | 现网 404 | 不泄露家庭名 | — | 既有扩 |
| F7 | 422 / 缺孩 | 现网 `child_required` 等 | 确认步已拦；直调当失败不白屏 | `err_no_child`（沿用） | `test_daily_upload_child_required` |
| F8 | 503 / 存储失败 | 5xx / 现网「照片无法保存」 | 本张失败；已成功保留 | `fail_one` | `test_daily_upload_storage_error_no_rollback` |
| F9 | 断网 | 无完整响应 | 不假装整批成功；已成功保留 | `fail_one` / `fail_go` | `test_daily_upload_offline_keeps_successes` |
| F10 | 多进程滚动发布 | 混合 404 无 code / 5xx | **不当永久无功能**；可重试 | — | `test_js_404_without_code_retryable_daily_upload` |
| F11 | 单张原文件 >20 MiB | 通常无请求；直调 400 | 确认步 `err_size`，先拿掉再放；UI 不写 KB/MB | `err_size` | `test_daily_upload_over_20mib_blocked_client` |
| F12 | 非图 / 损坏 | 通常无请求；直调 400 | 跳过该张，其余可提交 | `err_bad` | `test_daily_upload_bad_file_skipped` |
| F13 | 压缩失败 | 无（尚未发或改传原件） | 原件 ≤帽则回退原件；否则该张失败 | `fail_one` | `test_daily_upload_compress_fallback_original` |
| F14 | 压缩后仍 >20 MiB | 400 托底 | 该张失败，不假装成功 | `fail_one` | `test_daily_upload_compressed_still_over_cap` |
| F15 | 单批 >50 张 | 无请求 | 口语拦；不写技术数字进体积单位缩写 | `err_batch`（本刀新增口语，见 §8） | `test_daily_upload_over_50_blocked_client` |
| F16 | 部分失败汇总 | 各张独立 201/失败 | 进度签 `fail_one` + `fail_go`；不回滚成功 | `fail_one` / `fail_go` | `test_daily_upload_partial_success` |
| F17 | 细线非整批比例 | — | 「正在放入 2/5」时细线≠40%，跟当前张 xhr 进度 | `progress` | `test_daily_upload_progress_line_is_current_file` |
| F18 | 界面禁压缩率 / KB / 画质开关 | — | 产品 UI 0 命中 | — | `test_copy_no_size_or_quality_jargon` |
| F19 | 成功去向 | — | 落到当天齐格且可见新图；不回空白首页当完成态 | `done_toast` | `test_daily_upload_lands_on_day_grid` |
| F20 | 家人路径去向 | — | 落到本人相册当天齐格；不进孩子时光线 | `self_banner` / `self_album` | `test_self_upload_lands_on_self_day_grid` |
| F21 | 天页 1/2/≥3 同一套齐格 | — | 无 4:3 / 对半 / 左大右二 / +N | — | `test_day_grid_same_template_no_plusn` |
| F22 | 时光线当天卡不铺开 | — | 缩略 ≤4 +「共 N 张」；点卡进天页；无票根 | `count` | `test_timeline_day_card_row_not_stack` |
| F23 | 只读可见性 | — | 无入口；可看格与卡 | `ro_meta` | QA + F4 |
| F24 | CSRF / 会话过期 | 现网 403 | 不假装成功 | — | 既有扩 |
| F25 | 禁词 | — | 会员\|开通\|商城\|发现\|广告 = 0 | — | `test_copy_forbidden_words_upload_day_slim` |
| F26 | 不改导入 / 日堆签 / 看谁签 / 视频主路径 | 既有路由行为不变 | — | — | 回归：legacy-import、filter、video |
| F27 | 滚动发布期间上传 | 新旧进程短暂并存 | 已 201 保留；失败可感知 | — | 同 F10 |
| F28 | 内存：禁止整批读入 ArrayBuffer | 单张限读 | 逐张压缩+上传；不一次解码整批 | — | `test_daily_upload_no_batch_buffer` |

**Cut 失败路径映射：** 只读写→F4/F23；非成员→F5；单张失败/中断→F2/F8/F9/F16；超帽→F11；损坏/非图→F12。

---

## 8. 文案（COPY 1:1 final · 产品键）

新建 `app/upload_day_slim_copy.py`（或等价模块），由 `constants` 再导出。产品文案与 mockup `COPY` 对齐；`anno_*`、示例人名/年龄/稿内桌面壳不进产品常量。动态句用占位，不把稿中「11月14日」写死。

| key | 文案 / 规则 |
|---|---|
| `confirm_title` | 放到这一天 |
| `date_lab` | 哪一天 |
| `date_today` | 今天 · {mo}月{d}日（非今天则只写日期，不写「今天」） |
| `submit` | 放进这一天 |
| `submit_self` | 放到我的相册 |
| `pick_n` | 已选 {n} 张 |
| `progress` | 正在放入 {i}/{n} |
| `fail_one` | {ok} 张已放入，有 {fail} 张没成功（按实际张数） |
| `fail_go` | 去看已放入的 |
| `done_toast` | 已放进今天（所选日不是今天时：已放进这一天——若需区分，用同一 toast 不写日期数字单位；实现优先 `已放进今天`，非今日用「已放进这一天」，仍禁体积缩写） |
| `err_size` | 有一张太大了，先拿掉再放 |
| `err_bad` | 有一张不是照片，已跳过。其余可以继续放。 |
| `err_batch` | 一次先放五十张就好，多的下次再放（**本刀新增**；final 表无此条，避免无上限选图撑爆内存；不写「50」阿拉伯数字也可，但须口语、禁 KB） |
| `self_banner` | 这些照片记在你名下，不会出现在孩子时光线。 |
| `self_album` | 家人相册 · {name} |
| `count` | 共 {n} 张 |
| `empty_tl` | 还没有照片 |
| `empty_cta` | 放入第一张 |
| `swipe` | 左右滑，看这一天 |
| `lb_count` | {i} / {n} |
| `ro_meta` | 只读 · 只能看（沿用） |
| `byline` | {who}传（沿用现网） |
| `strip_label` / `strip_hint_pre` | 贴上兄弟姐妹 / 已贴{name}，可再贴兄弟姐妹（胶片贴沿用，本刀不改交互规则） |

沿用、本刀不重写：看谁签 `who_*`、胶片贴其它 hint、`cancel`、既有 403 页句。覆盖日常上传主钮/横幅时，以本表为准（替换「贴上这一天」「贴着…」「记在本人名下」等旧句）。产品 UI **不出现**「会员」「开通」「商城」「发现」「广告」，也不把压缩率、体积字母缩写（KB/MB）、画质写进产品文案。服务端托底「20MB」字符串不在产品确认/进度/失败签上原样露出。

---

## 9. 验收（AC）与测试计划

### 9.0 门禁测试纪律

测试一开始就隔离临时目录。交 review 前在共享环境全量 pytest **连跑 3 次**，并按历史 QA/review 问题自查（含 COPY 禁词、权限三档、导入/看谁签/视频回归、失败路径 F1–F28）。

### 9.1 AC ↔ 测试

- AC-E1 简洁：确认步 ≤3，无第二向导、无画质/压缩设置。  
- AC-E2 一行状态：progress / self_progress / desk_progress / fail_one；细线跟当前这张（F17）。  
- AC-E3 默默压缩：无开关；可传的图能完成；超帽 `err_size`。  
- AC-E4 成功去向：落到当天齐格 + toast + 细描边（F19/F20）。  
- AC-E5 天页网格：1/2/≥3 同一套；可点；lightbox 左右滑；无 +N；非瀑布流。  
- AC-E6 时光线当天卡：一小排 +「共 N 张」，不铺开，不另起票根。  
- AC-E7 一看就会：主钮底部；手机一屏走完确认提交；天页一屏能看懂有哪些图；时光线不被当天多图挤长。  
- AC-E8 双端：390 与 ≥1024（Playwright Firefox+WebKit 或既有套件）。  
- AC-E9 权限：只读无入口可看；非成员现网拒绝码。  
- AC-E10 COPY：禁词 0；无压缩率/KB/画质。  
- AC-E11：tag `v2026.11.14.1` + 发布日志 + 可回退 `.9`（禁 DROP / `down -v`）。

### 9.2 四角色机器人走查

管理员 / 可上传 / 只读长辈 / 非成员；记体验卡点。家人「记在我名下」必走。

### 9.3 验收节（写死）

`releases/v2026.11.14.1.md` + `CHANGELOG.md` + OpenAPI `version="2026.11.14.1"`。实现说明随 PR。tech / 文档同步更新。

---

## 10. 待决问题

### 10.1 已关（cut / final / 本刀拍板）

| # | 结论 |
|---|---|
| Q1 | **已关：** **零新表零新列**；进度/压缩/新图描边均客户端；描边靠 `?new=`，不入库。 |
| Q2 | **已关：** 写接口复用现网 `POST /api/photos`；不新增写接口；不把 peek/EXIF 分堆搬进日常上传。 |
| Q3 | **已关：** 默默压缩在浏览器内、提交后逐张进行；确认步原文件 >20 MiB 走 `err_size` 先拿掉；已过确认的图再压；参数见 §3.2。界面无开关、不展示率/体积。 |
| Q4 | **已关：** 进度细线 = 当前这张上传字节进度，不是整批 i/n 比例；压缩等待时细线在 0，文案仍「正在放入 i/n」。 |
| Q5 | **已关：** 成功去向：孩子 → `/children/{id}/days/{taken_on}`；家人 → **新增读页** `/people/{uid}/album/days/{taken_on}`（零新表；因现网相册是整本平铺、family feed 不承担 self 当天齐格）。 |
| Q6 | **已关：** 天页邮票齐格 3/5 列，1/2/≥3 同一套；取消 +N / 4:3 / 对半 / 左大右二为主形态；日期只出现一次 +「共 N 张」；页内 lightbox。 |
| Q7 | **已关：** 时光线当天卡沿用现网信息结构 + 一小排约 4 枚 +「共 N 张」；不铺开、不另起票根、不用 +N 遮缩略；看谁签/往年今日卡不改。 |
| Q8 | **已关：** 主路径按入口上下文定 kind（孩子 FAB→child，家人相册 FAB→self），砍掉 FAB 前的「照片放到哪里？」第二向导。 |
| Q9 | **已关：** 权限三档不变；只读无入口可看；非成员拒绝码与现网一致（不改 403.html）。self 仍不进孩子时光线。 |
| Q10 | **已关：** 体积帽沿用 20 MiB；单批客户端 50 张；并发=1；格式 jpeg/png/webp。 |
| Q11 | **已关：** COPY 按 §8；禁词见上；`err_batch` 为工程口语补键，须过禁词扫描。 |
| Q12 | **已关：** 不改日堆签/旧库导入、看谁签、日志/一日一句/视频主路径、编辑照片主流程。不回头改 PR#18。 |
| Q13 | **已关：** 微信仍停；无商城/发现/广告/会员。 |
| Q14 | **已关：** 禁 `docker compose down -v` / DROP；回退 `.9` 不 DROP。 |
| Q15 | **已关：** cut 两份哈希一起写：锁体（In/AC 认据）`868647b250a086331473737fdb16f0e6`；全文件（含设计 final）`036cc191936390a58dfb8167c200f9d2`。final mockup `6f2a5be00e2c35a9f1816c3a20ce283f`。过程稿不作认据。`ro_day` 按普通顶栏 + 一枚只读签。 |
| Q16 | **已关：** box 无完整 clone；**实现时对照 main tip `687a063a`** 核实 `family_child_photos`、上传 JS、天页/时光线模板（执行提醒，非开放产品问题）。 |

### 10.2 仍开

（无。**§10 全关**。）

对照 cut「已拍」：第十四刀前端精简（窄）✓；一行状态不写压缩率/KB、不加面板 ✓；默默压缩 ✓；天页网格、取消 +N ✓；时光线当天卡一小排+张数、不铺开 ✓；成功去向 ✓；权限 ✓；不改日堆签/看谁签/数据模型 ✓；体验三约束 ✓。无残留须 PM 再拍项。技术选定（API 复用 / 客户端压缩参数 / 家人当天齐格读页 / 零新表 / 体积帽 / 微信停 / 禁 down -v）**全部写死，勿再开放 §10**。

**开 CloudAgent 门禁自检：** final md5 已锁 ✓ · PM 拍板已进 cut ✓ · **§10 全关** ✓ → 仍须 **本 tech 过「时光机研发」实现前审** 后才开 CloudAgent。  
**本任务明确：只落盘 tech，不开 CloudAgent、不改生产代码、不 git、不 clone、不开 PR。**

---

## 11. 发布与回退

- 分支 PR → review 实现前审 →（门禁过后再）CloudAgent 实现 → 实现后审 / 双 QA（管理员 / 可上传 / 只读长辈 / 非成员；含 390 与 ≥1024）→ 终审 → 合并，打 annotated tag **`v2026.11.14.1`**；OpenAPI `version="2026.11.14.1"`；写 **`releases/v2026.11.14.1.md`**（**非 draft**）+ **`CHANGELOG.md`** 条目 + 实现说明随 PR。  
- 基线：`v2026.10.25.9` @ `687a063a`。  
- 升级（线上）：备份 DB 与媒体 → checkout tag → `python scripts/migrate.py`（应打印 `upload_day_slim ready (no new tables)`，库哈希不变）→ 重启 `family-album.service`。不改生产密钥。禁 DROP / `down -v`。compose 环境按既有文档 rebuild，同样不 `down -v`。  
- **回退到 `v2026.10.25.9`：** checkout 该 tag 并重启；**零新表**；**不要** `down -v` / `DROP TABLE` / `DROP COLUMN`；保留媒体与 DB。

**发布日志要点（计划写入 `releases/v2026.11.14.1.md` + CHANGELOG）**

- 日常传图：选图 → 确认 → 完成；进行中一行「正在放入 i/n」，底边细线跟当前这张；默默压缩到可传体积，无画质/压缩开关，不展示压缩率或 KB。  
- 完成后落到当天齐格，提示「已放进今天」，新图细描边。  
- 某一天页改为邮票齐格（1/2 张同一套），取消左大右二 / +N 藏图；点开可左右滑。  
- 时光线当天卡改为一小排缩略 +「共 N 张」，点进去再看当天，不铺开。  
- 家人「记在我名下」同一套进度与当天齐格；只读无入口可看；权限三档不变。  
- 零新表零新列；回退 `.9` 不 DROP；禁 `docker compose down -v`。  
- 硬闸词 0；微信仍停；不改日堆签 / 看谁签 / 旧库导入 / 视频主路径。

**已知限制**

- 压缩在客户端，旧浏览器若无 `createImageBitmap` 须有 canvas 回退；不能解码则回退原件（在帽内）或该张失败。  
- 压缩后入库的是 JPEG 字节（原 png/webp 可能被转 JPEG）；回退 `.9` 后这些文件仍可按普通照片显示。本刀不保存「压缩前原件」第二份。  
- 单批张数/选图主闸在客户端；服务端托底单张 20 MiB、鉴权与格式。  
- `anno_*` 不进产品页。  
- 线上公网上行慢的根因不在本刀改带宽；本刀用默默压缩 + 当前张字节进度缓解可感知的「只转圈 / 以为卡住」。

---

## 12. Unlock（实现顺序；仅门禁通过后）

1. COPY 模块 + 禁词/禁体积缩写测  
2. 日常上传确认步收口（多选、日期芯片、胶片贴/家人路径、砍第二向导）  
3. 默默压缩 + 一行进度签（细线跟当前张，并发=1）+ 部分失败口径  
4. 成功去向：孩子天页 + 本人相册当天齐格读页；toast + `?new=` 细描边  
5. 天页邮票齐格 + 骨架 + lightbox；时光线当天卡改一小排  
6. 只读 / 非成员 / 双端；全量 pytest ×3；回归导入/看谁签/视频；releases + CHANGELOG + version  

**再次强调：** §10 已全关；未过实现前审前，**禁止**开 CloudAgent；本任务只落盘 tech，不改业务代码、不 force push、不 clone GitHub、不开 PR、不开 CloudAgent。

---

## 实现门禁表（汇总）

| 门禁项 | 状态 | 证据 |
|---|---|---|
| final mockup.md5 已锁 | ✓ | `6f2a5be00e2c35a9f1816c3a20ce283f`（落盘前实测；11:29 已核 PASS「齐格签」） |
| cut 身份锁（两份哈希） | ✓ | 锁体 In/AC 认据 `868647b250a086331473737fdb16f0e6`；全文件含设计 final `036cc191936390a58dfb8167c200f9d2`（PM 11:34 确认） |
| PM 拍板已进 cut | ✓ | 定稿 11:03；PM2 11:30 可交工程；PM 11:34 再确认哈希与范围 |
| tech §10 全关 | ✓ | §10.2 无开放项（API 复用 / 客户端压缩 / 家人当天齐格读页 / 零新表 / 体积帽 / 微信停 / 禁 down -v 均已关） |
| 本 tech 过「时光机研发」实现前审 | ☐ | **通过前禁开 CloudAgent** |
