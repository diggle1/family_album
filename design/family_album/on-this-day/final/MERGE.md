# MERGE · 往年今日（窄）v2026.10.25.5 FINAL「琥珀年签」

**路径：** `/workspace/design/family_album/on-this-day/final/`  
**规则：** cut「往年今日（窄）」v2026.10.25.5 + 合并取舍；定名 **「琥珀年签」**（designer2 主线；撕边浅纸单卡 + 琥珀年号戳）；吸 designer「旧相框卡」大年份可读 + 缩略拼贴嵌卡内（**不作**木色相框主形态）。两份源稿**只读未改**。  
**不写生产代码；不改 cut；不 git；不碰 elders-whats-new/final · elders-no-app/final · photo-multi-child/final。**

## 源稿锁定

| 稿 | 路径 | mockup.md5 |
|---|---|---|
| designer「旧相框卡」 | `on-this-day/v-designer/` | `d062066564b0fc0b4db7767c750a93b3` |
| designer2「琥珀年签」 | `on-this-day/v-designer2/` | `e5ccd921edb4ffa72d3edee96f7b35e6` |
| **FINAL** | `on-this-day/final/` | `33ebfc7cbe4b5ed25dc503ad73386cf8` |

## 对比表 · 取舍

| 方面 | designer「旧相框卡」 | designer2「琥珀年签」 | **FINAL 取** | 理由 |
|---|---|---|---|---|
| 主形态 | 木色细框 + 缩略拼贴卡 | **撕边浅纸单卡 + 琥珀年号戳** | **琥珀年签**：撕边单卡 + warn 双线圆印戳 | 对齐门口笺/日签条撕边族；与日签条可感知差（单卡 vs 顶条） |
| 年份可读 | 右上小印章「2025」 | 左圆印年号（偏小） | **吸旧相框：大年份数字**（戳内 16px/900） | 长辈一眼会 |
| 缩略 | **缩略拼贴一眼见有图** | 无缩略，仅角标 | **吸旧相框：缩略拼贴嵌进年签卡内** | 一眼见有图；**不**改成木色相框主形态 |
| 文案 | 「去年今天」+「看看那时…」 | 「去年今天」+ 月日·年 | **「去年今天」** + 副文月日·年 | cut 口语 |
| 有才占位 | 无则不挂 DOM | 同 | **两边一致** | AC-D2 |
| 与日签并存 | 日签上、相框下 | 日签上、年签下 | **日签上、年签下；不合成** | AC-D4/D7 |
| 桌面 | 内容柱同卡 | 内容柱同序；非右栏 | **内容柱（~720）同序** | AC-D6 |
| 某一天 | 复用既有 | 同 | **同** | cut |
| 孩子页 | 过滤角标 | 同 | **同** | AC-D8 |
| 禁词 | 硬闸 | 同 | **同** | D7/D8 |

## 放弃（明示）

- 木色相框作主形态 → **不作**（缩略仅嵌年签卡内）
- 满宽系统 Banner / 暖黄横幅日牌复刻作主控件 → **不作**
- 第二套导航 / 新底部 Tab → **不作**
- 右侧独立「往年」栏 → **不作**
- 合成一条 Feed / 两卡并成一条 → **不作**
- 吓人空态弹窗 → **不作**
- 多年度列表 / 跨年滑动 / AI 文案 / 推送 → **Out**
- 回忆 Feed / On This Day / 会员|开通|商城|发现|广告 → **硬闸**
- 微信小程序屏 → **本刀不做**

## 自检命令（在 `final/` 下）

```
md5sum mockup.html
rg -n "会员|开通|商城|发现|广告|回忆 Feed|On This Day" mockup.html
# 期望：COPY 对象与产品 UI 字符串 0；脚注自检句可列禁词
md5sum ../../elders-whats-new/final/mockup.html
# 期望仍为 ac2ceba835f45d368d82c98660a7c32d（本刀未碰）
md5sum ../../elders-no-app/final/mockup.html
# 期望仍为 3f41b99ba967c82e804dc80f29bd02be（本刀未碰）
md5sum ../../photo-multi-child/final/mockup.html
# 期望仍为 f700e4de1b74ac0923745d91c63a0e20（本刀未碰）
```

## 交付清单

`flow.md` · `spec.md` · `mockup.html`（9 组 · `#s=` / `#s=all` · COPY）· `shots/`（每组 ≥1 + mockup-full；含 390 与 ≥1024）· `MERGE.md`（本文）。

## 自检结果（2026-10-25 12:19 Asia/Shanghai）

- mockup.md5：`33ebfc7cbe4b5ed25dc503ad73386cf8`
- 9 组 · 10 帧 · 45 COPY keys；shots 9 组 + mockup-full.png（390 + ≥1024 齐）
- 禁词「会员|开通|商城|发现|广告|回忆 Feed|On This Day」：COPY 区与产品 UI **0**；仅脚注自检句列禁词
- 取舍落点：琥珀年签撕边单卡+年号戳+缩略 `#s=tl_otd` ✓；日签上·年签下 `#s=tl_both` / `_desk` ✓；无卡不占位含 leap `#s=tl_none` ✓；某一天有料 `#s=day` ✓；某日空 `#s=day_empty` ✓；孩子页 `#s=child_filter` ✓；403/deny `#s=err_perm` ✓；桌面内容柱 `#s=tl_otd_desk` `#s=tl_both_desk` ✓
- elders-whats-new/final mockup.md5 仍为 `ac2ceba835f45d368d82c98660a7c32d`（未碰）
- elders-no-app/final mockup.md5 仍为 `3f41b99ba967c82e804dc80f29bd02be`（未碰）
- photo-multi-child/final mockup.md5 仍为 `f700e4de1b74ac0923745d91c63a0e20`（未碰）
- 源稿只读：designer `d062066564b0fc0b4db7767c750a93b3` · designer2 `e5ccd921edb4ffa72d3edee96f7b35e6` 未改
- cut md5 仍为 `36a6fb5511ba65ce5229a384ffc5d888`（未改）
- UI.md：已同步追加往年今日行；过程稿两源 md5 脚注；跨刀加「琥珀年签」
- README.md：已代补一行
- 待 PM：无
- 赶上 14:00：是（约 12:19 锁）

## designer 核稿（2026-10-25 12:23 Asia/Shanghai）

- **结论：PASS · 已核**
- mockup.md5 核符 `33ebfc7cbe4b5ed25dc503ad73386cf8`（琥珀年签）
- AC-D1–D9 帧齐；双端 390+≥1024；日签上·年签下；无料不占位；禁词 0；未碰 .2/.3/.4 final
- 取舍：琥珀年签主线 + 吸大年份/缩略（不作木色相框主形态）— 与日签撕边顶条可感知差（单卡+戳+缩略），可交工程
- 群一度「作废·重锁」指令已按监工最新认据撤回；README / UI.md 行已标 **已核**
- 工程只拿本 md5；勿开旧相框重锁
