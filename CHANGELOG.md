# Changelog

## v2026.10.24.1 — 2026-10-24

免费导出 UI（AC-E1–E5）。相对 `v2026.10.23.2`，不改家庭域、角色等级和媒资读写约定。无新表。

### 加入

- 设置页「我的数据」→「导出我的照片」，徽标「永久免费」。任意成员可见，包括只读。
- `GET /api/export/my-photos.zip` 只打包当前成员在本家庭上传的照片。文件名 `{taken_on}_{photo_id}{suffix}`。先写完临时文件再回传。查询忽略请求里的 `family_id`。
- 本人 0 张：页面是空态（去时光线保存单张），ZIP 接口 404 `{"detail":"no_photos","count":0}`，不生成空包。非成员先被拒绝，永远是 403。
- 打包超时（`EXPORT_BUILD_TIMEOUT_S`，默认 120，可用同名环境变量覆盖）或任一张读失败：终止打包线程，503 `{"detail":"export_failed"}`，不发出 ZIP 字节。临时文件在回传完成、客户端中断、失败三条路径删除。
- 大图溢出菜单「保存原图」。`GET /media/{id}/download` 为附件。别家 id 与不存在的 id 都是同一个 404；非成员仍是 403。`GET /media/{id}` 仍是 inline。
- 响应 `Cache-Control: private, no-store`。读取走 `store_for(photo.storage_backend)`。
- BUG-1：断网、下载中途掉线，或响应头还没回来时，打包中和失败 Sheet 是页面里预置的 `<template>`，失败 Sheet 立刻出现，不再请求服务器。等响应头超过 130 秒（大于服务端 `EXPORT_BUILD_TIMEOUT_S` 的 120 秒）算失败；拿到响应头之后，15 秒没有新字节也算失败。重试在本地重新发起导出。

### 不做

全家备份、日期或成员多选、视频与日志导出、配额墙、商城、发现、广告、公开分享链接。不改部署、存储抽象和权限模型。

### 回退

见 `releases/v2026.10.24.1.md`。无数据库迁移。回退代码不会删除已有照片。

## v2026.10.23.2 — 2026-10-23

部署与存储补刀（AC8–AC10）。相对已发布的 `v2026.10.23.1`，并带上 main 上已经落地的登录安全加固。

### 加入

- `Dockerfile`、`docker-compose.yml`、`.env.example`。数据卷 `app-data` 挂在 `/data`；`STORAGE_BACKEND=s3` 时这只卷仍然挂着，旧的本地文件还能读。healthcheck 只请求 `GET /healthz`。
- 媒资抽象：写入走 `current_store()`，读取按 `Photo.storage_backend` 走 `store_for()`。支持本地目录和 S3 兼容存储。看图仍是登录后的 `GET /media/{id}`，不写公开直链或预签名 URL。
- `scripts/migrate.py` 在 `create_all` 之后幂等执行 `ALTER`，给已有 `photos` 表补上 `storage_backend`（默认 `local`）。从 `.1` 升级必须跑迁移；compose 启动时会跑。
- 存储探测失败不让进程退出。`/healthz` 只表示进程和数据库可用。失败时业务页进入 `/errors/storage?code=`；`/api` 或 `Accept: application/json` 返回 `{code, message}`。
- `/setup` 六步清单（含高熵 `SECRET_KEY` 和临时 `AUTH_TEST_CODE=000000`）。管理员在本会话完成登录、孩子照上传和 `/media` 200 后可打开 `/setup/ok`。不新增 `setup_confirmed_at`。
- 设置页仅家长管理员有一行灰字，说明存储在 `.env`。没有存储表单、测通或健康仪表盘。
- `scripts/seed_roles.py`：`admin@example.test`、`uploader@example.test`、`viewer@example.test`、`outsider@example.test`。一人仍然最多一个家庭。
- 登录安全加固（此前在 main，未单独发版）：`AUTH_TEST_CODE` 默认空；默认 `SECRET_KEY` 在未设置 `ALLOW_INSECURE_DEFAULTS=1` 时拒绝启动；验证码 HMAC 与限流；`COOKIE_SECURE`。

### 不做

应用内存储设置、测通、健康三灯、媒资迁移向导、导出界面、日志、视频、评论、商城、发现、广告、多租户 SaaS。不拆除「一人一家庭」。

### 回退

见 `releases/v2026.10.23.2.md`。回退代码不会删除数据卷，也不会删除 S3 上的对象。

## v2026.10.23.1 — 2026-10-23

主线最小闭环，空仓上的第一版。

### 加入

- 邮箱验证码登录。`AUTH_TEST_CODE`（开发默认 `000000`）为测试旁路，不发送真实邮件。
- 一个用户至多一个家庭。创建或接受邀请时若已有家庭，返回 400。
- `GET/PATCH /api/me`：姓名必填（1–40 字）后页面才进入时光线；`relation_label` 可空，是家庭级展示，不绑定孩子。
- 邀请、三档权限（`admin` / `uploader` / `viewer`）。不能降级家庭内最后一位家长管理员。
- 孩子档案与按天时光线，年龄文案为「N岁M个月」，未满月为「不足1个月」。
- 孩子照片必须带拍摄日期；孩子多于一个时必须指定孩子。单孩可省略 `child_id`，仍会落库。
- 家人本人照片。传入非空 `child_id` 时返回 400。默认拍摄日为 Asia/Shanghai 的当天。本人照片不进入孩子时光线。
- 照片只经登录后的 `GET /media/{id}` 读取，无公开直链。
- 非成员访问家庭内容返回 403，响应不包含家庭名、孩子名或成员列表。
- 页面：登录、完善资料、家庭空态、添加孩子、时光线、上传分流、家人列表、本人相册、设置、无权限页。

### 不做

日志正文、视频、评论、真实 SMTP、公开链接、发现、AI、旧库导入、导出界面。路线图要求自己的媒资可以永久免费导出，本版只是还没有导出界面，也没有收费入口。

### 回退

见 `releases/v2026.10.23.1.md`。空仓基线是 `db09d34`。代码回退不会删除本机的 `data/app.db` 和 `data/photos/`。
