# 前端精简（窄）v2026.11.14.1 · FINAL 流程「齐格签」

依据：cut `2026-11-14-前端精简-cut.md` v2026.11.14.1（md5 `868647b250a086331473737fdb16f0e6`）。cut 优先。  
稿件：`flow.md` · `spec.md` · `mockup.html`（`#s=` / `#s=all`）· `shots/` · `MERGE.md` · `README.md`。  
**不改 cut；不写生产代码；不 git。** 时区 Asia/Shanghai。过程稿不作工程认据。UI.md 本刀已由 designer 盖 **已核**。

## 主张：齐格签

- **入口**：admin / uploader 时光线 FAB 选图后进入确认；只读无入口。看谁签仍在时光线顶，本刀不改。  
- **确认**：已选小邮票一行 + 日期芯片「今天 · 11月14日」+ 胶片贴挂谁。主钮固定底部「放进这一天」。无第二向导，无画质/压缩开关。手机一屏走完。  
- **进度签**：进行中只占一行暖纸票，「正在放入 2/5」；底边细线跟的是**当前这张**，不是整批填到 2/5。  
- **完成**：落到当天齐格；短提示「已放进今天」+ 新图细描边。  
- **天页**：等大邮票齐格；1 / 2 / ≥3 同一套；日期只出现一次并带「共 N 张」；多了下滑；点开大图可左右滑；加载用同格骨架。  
- **时光线**：沿用现网当天卡（日期+周几+年龄胶囊+署名），照片区一小排缩略 +「共 N 张」，点进天页，不铺开全部，不另起票根。  
- **家人**：记在我名下，无胶片贴，主钮「放到我的相册」，同一套进度签，完成回个人相册当天。  
- **不改**：日堆签 / 旧库导入、看谁签、权限与数据模型、日志 / 一日一句 / 视频主路径。微信停。

## 状态机

```
时光线（看谁签 + 当天卡一小排 + FAB）          #s=tl
  空态（可传有入口）                          #s=tl_empty
  └─ 选图 → 确认（挂谁、哪天）                #s=confirm
        ├─ 非图 / 损坏 → err_bad（跳过该张，其余可提交）
        ├─ 单张超过 20 MiB → err_size（口语拦，先拿掉再放）
        └─ 提交 → 进度签                      #s=progress
              ├─ 单张失败 fail_one（签上可感知 + 去看已放入的）
              └─ 完成 → done_day（当天齐格 + 「已放进今天」+ 新图细描边）

家人「记在我名下」                            #s=self → self_progress → self_done
看：当天卡 → 天页齐格（day_1 / day_2 / day_grid / day_many）
    加载 day_load → 大图 lightbox
只读：ro_tl / ro_day（无 FAB，可看）
非成员：err_perm（现网拒绝句，不露家庭名）
桌面同路径：desk_confirm / desk_progress / desk_day / desk_tl
```

步骤始终 ≤3：选图 → 确认 → 完成。

## 角色 × 界面

| 角色 | 日常上传 | 看天页齐格 / 时光线当天卡 | 删照片 |
|---|---|---|---|
| admin | ✓ | 本家 | 全部（本刀不改） |
| uploader | ✓ | 本家 | 仅自己的（本刀不改） |
| 只读长辈 | ✗（无入口；无 FAB） | 本家 | ✗ |
| 非成员 / 移出 | ✗（拒绝码与现网一致） | — | — |

## 失败 → 屏

| 路径 | key | 屏 |
|---|---|---|
| 单张失败 / 中断 | fail_one / fail_go | #s=fail_one |
| 非图 / 损坏 | err_bad | #s=err_bad |
| 超过单张 20 MiB | err_size | #s=err_size |
| 只读 | ro_meta | #s=ro_tl · #s=ro_day |
| 非成员 / 移出 | err_perm | #s=err_perm |

失败可感知；其余可继续或已放入的可去看；不假装成功；不丢进未知页。超过体积帽只口语提示，不另开说明面板，不写 KB。

## 帧（24）

tl · tl_empty · confirm · progress · fail_one · err_size · err_bad · done_day · self · self_progress · self_done · day_1 · day_2 · day_grid · day_many · day_load · lightbox · ro_tl · ro_day · err_perm · desk_confirm · desk_progress · desk_day · desk_tl

## AC → 屏

- AC-E1 简洁：confirm / self / desk_confirm（步骤≤3，无第二向导、无画质设置）  
- AC-E2 一行状态：progress / self_progress / desk_progress / fail_one（细线跟当前这张）  
- AC-E3 默默压缩：界面无开关；超帽见 err_size  
- AC-E4 成功去向：done_day / self_done / desk_day  
- AC-E5 天页网格：day_1 / day_2 / day_grid / day_many / lightbox / day_load  
- AC-E6 时光线当天卡：tl / desk_tl  
- AC-E7 一看就会：confirm 主钮底部；day_* 一屏能看懂有哪些图；tl_* 不被多图挤长  
- AC-E8 双端：上列手机帧 + desk_*  
- AC-E9 权限：ro_tl / ro_day / err_perm  
- AC-E10 COPY：全稿产品文案走 COPY，禁词 0
