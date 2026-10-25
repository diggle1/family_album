# 时光机（family_album）

家庭成长相册：以孩子为主，也收进家人分享的照片。当前版本 `v2026.10.24.3`。

技术依据是部署与存储 tech r2（SHA256 `76772993db93cedb5c484c67e5ef18f9bd27611d51dca813b3237084eb486738`）。`v2026.10.24.3` 在本人照片免费导出之上，加上只在本家庭内可见的成长日志。此前已有 docker compose 自部署、本地 / S3 媒资，以及按 `family_id` 隔离的读图。不做视频、评论、真实发信、公开直链、发现、商城、广告、媒资迁移向导，也不做应用内的存储设置。

## 本版能做

- **家庭成员与关系**。一人一个家庭（`Membership.user_id` 保持唯一）。同一实例可以并存多个家庭。成员填写姓名（1–40 字），并可写家庭内关系（如爸爸、外婆）。关系记在成员上，不绑定某个孩子。已经有家庭时，再创建或接受邀请会返回 400。
- **三档权限**。`admin` 家长管理员可以邀请、改档、加孩子、上传；`uploader` 可上传孩子照片和本人照片，不能改别人的权限；`viewer` 只读，页面不渲染「＋」。家庭里最后一位家长管理员不能被降级。
- **孩子照与本人照，按拍摄日展示**。孩子照片必须有拍摄日。家人本人照片不会出现在任何孩子的时光线里。
- **鉴权读图**。`GET /media/{id}` 要求登录，并且 `photo.family_id` 等于当前成员的 `family_id`。未登录和非本家庭成员返回 403。没有公开直链，也不把预签名 URL 写进页面。
- **自部署**。`docker compose up -d` 拉起后，按清单登录、上传一张孩子照、再打开 `/media`。
- **成长日志**。家长和可上传成员可以给一个孩子写纯文字，并引用家里已有的 0–9 张照片。日志和照片按天出现在时光线里。只读成员只能看。删日志不会删照片。导出本人数据时，ZIP 里多一个只含自己日志的 `日志.md`。

## 一键自部署

需要 Docker。干净机器上的验收路径是：

1. 准备一台能安装 Docker 的机器。
2. 复制环境文件并写入密钥：

   ```bash
   cp .env.example .env
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

   把输出写进 `.env` 的 `SECRET_KEY`。Phase A 自测可以临时写 `AUTH_TEST_CODE=000000`，生产必须清空或不设置。不要设置 `ALLOW_INSECURE_DEFAULTS`，也不要把真实密钥写进镜像。
3. `docker compose up -d`
4. 浏览器打开 `http://<host>:8000`
5. 用管理员账号登录。开了临时测试码时，先发送验证码，再输入 `000000`。
6. 上传一张孩子照，用登录态打开 `/media/{id}`，应返回 200。

同一份清单在网页 `/setup`。管理员在本会话里完成「登录 + 成功上传一张孩子照 + `/media` 200」后，可以打开 `/setup/ok`（「装好了，可以给家人用了」）。这是会话标记，没有 `setup_confirmed_at` 字段。

`GET /healthz` 只检查进程和数据库，compose 的 healthcheck 只打这条路径。媒资卷或 S3 连不上时进程仍会起来，容器保持 healthy，浏览器看到 `/errors/storage?code=`。修 `.env` 或卷之后执行 `docker compose up -d`，或点「我改好了，刷新重试」。

### 从 v2026.10.23.1 升级

先备份数据卷，再迁移，再换镜像。只跑 `create_all` **不会**给已有的 `photos` 表加上 `storage_backend`。compose 的启动命令会执行 `python scripts/migrate.py`；不使用 compose 时要自己先跑。

```bash
python scripts/migrate.py
```

迁移是幂等的：缺列时执行 `ALTER TABLE photos ADD COLUMN storage_backend VARCHAR(16) NOT NULL DEFAULT 'local'`。已有行记为 `local`。本版不搬迁旧文件，切换存储只影响新上传。

## 存储

| 变量 | compose 示例 | 说明 |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` 或 `s3`。写入用当前值，读图用每行的 `storage_backend` |
| `MEDIA_ROOT` | `/data/media` | 本地根目录。未设置 `PHOTO_DIR` 时，进程默认也是 `/data/media` |
| `PHOTO_DIR` | （不设） | 若设置，本地根目录改用它。本机开发可设为 `./data/photos` |
| `DATABASE_URL` | `sqlite:////data/app.db` | 数据卷上的 SQLite。本机直接跑时默认 `sqlite:///./data/app.db` |
| `S3_ENDPOINT` / `S3_BUCKET` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 空 | 仅 `s3` 时必填 |
| `S3_REGION` | 空 | 可选，客户端默认 `us-east-1` |
| `S3_FORCE_PATH_STYLE` | 空 | MinIO 设为 `1` |

`docker-compose.yml` 始终把命名卷 `app-data` 挂到 `/data`。即使 `STORAGE_BACKEND=s3`，旧的本地文件仍从这只卷读取。默认 compose **不**附带 MinIO。

产品里没有「设置 → 存储」、测通或健康三灯。家长管理员的设置页只有一行灰字，指向本说明。可上传和只读成员看不到这行字。

失败页只显示键名和 `••••`，不显示密钥明文。

### 高级：MinIO（可选，不要写进默认 compose）

自行增加 MinIO 服务时，应用侧只改 `.env` 后重启：

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT=http://minio:9000
S3_BUCKET=family-album
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_FORCE_PATH_STYLE=1
```

上面的 MinIO 账号只适合本机试验。桶保持私有。看图仍走 `/media/{id}`。

## 本地开发（不用 compose）

需要 Python 3.12+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/migrate.py
export PHOTO_DIR=./data/photos
export AUTH_TEST_CODE=000000
export SECRET_KEY=dev-secret-change-me
export ALLOW_INSECURE_DEFAULTS=1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`ALLOW_INSECURE_DEFAULTS=1` 只给本机开发，用来放过默认的 `dev-secret-change-me`。`AUTH_TEST_CODE` 不设置时是空的，测试旁路关闭。`SECRET_KEY` 为空，或仍是默认值且没有上面的开关时，进程拒绝启动。

生产不要设置 `ALLOW_INSECURE_DEFAULTS`。上线前确认：

- `AUTH_TEST_CODE` 为空，或者不设置。
- `SECRET_KEY` 是高熵随机串（用上面的 `token_urlsafe` 命令生成）。
- 站点走 HTTPS 时设置 `COOKIE_SECURE=1`。本地 `http://127.0.0.1` 不要开。

限流按直接连上来的客户端地址计算，不读取 `X-Forwarded-For`。

## 四角色夹具

`python scripts/seed_roles.py` 写入一个示例家庭和四个账号，可重复执行。它不是生产默认数据。登录前临时设置 `AUTH_TEST_CODE=000000`，用完清空。

| 角色 | 邮箱 |
|---|---|
| 家长管理员 | `admin@example.test` |
| 可上传 | `uploader@example.test` |
| 只读长辈 | `viewer@example.test` |
| 非成员 | `outsider@example.test` |

## 登录

本版不发真实邮件。

1. 填写邮箱，请求验证码。服务端只存 HMAC-SHA256，响应里不返回验证码。默认 10 分钟有效。
2. 若设置了 `AUTH_TEST_CODE`，必须先请求过验证码，该值才能通过校验。
3. 同一邮箱两次获取至少间隔 60 秒。同一 IP 每小时最多 20 次、每天最多 50 次。超出返回 429。
4. 同一邮箱连续验错 5 次，或同一 IP 累计失败 30 次，在验证码有效期内拒绝继续校验。
5. 新的验证码会使该邮箱上一条失效。

登录后若还没有姓名，页面会停在「完善资料」。

## 权限

| 档位 | 能做的事 |
|---|---|
| `admin` 家长管理员 | 邀请、改档、加孩子、上传、装机确认页、设置里的一行存储说明 |
| `uploader` 可上传 | 上传孩子照片和本人照片 |
| `viewer` 只读 | 只看。页面不渲染「＋」 |

非成员访问家庭内容返回 403，正文只有「没有访问权限」。跨家庭访问 `/media` 同样是 403。切换本地和 S3 不改变这套 `family_id` 规则。

自己的媒资可以永久免费导出。设置 → 我的数据 → 导出我的照片。ZIP 只含当前成员在本家庭上传的照片，读文件按每张的 `storage_backend`。大图页「保存原图」下载原图。没有收费入口。

打包默认等待 `EXPORT_BUILD_TIMEOUT_S` 秒（默认 120，同名环境变量可改）。浏览器等响应头的时间是这个值再加 10 秒。前面的反向代理读超时要大于这 130 秒；nginx 默认 60 秒会把还在打包的请求切断。不要压缩或改写 `/api/export/`，也不要丢掉 `X-Export-*` 响应头。同一成员同时只跑一个打包，这个名额记在进程里，所以部署保持单进程。启动时只清理修改时间早于打包超时加发送窗口的普通临时 zip，不删符号链接，也不删别的进程刚打好的包。临时目录可用 `EXPORT_TEMP_DIR` 指定。文件名里的日期优先用请求的 `d=YYYY-MM-DD`，不合格时用 `EXPORT_FILENAME_TZ`（默认 `Asia/Shanghai`）。`d` 不参与筛选。

## 照片

- 只接受 jpeg / png / webp，单张不超过 20MB。
- 家长管理员和可上传的家人可以从本机一次导入旧照片（最多 50 张）。整批要手选同一天，并至少挂给一个孩子。前面的反代请把 `client_max_body_size` 设到不少于 200m，并加上 `proxy_request_buffering off`。说明见 `releases/v2026.10.25.7.md`。
- 文件名是随机 UUID。`storage_backend` 在上传时写成当时的 `STORAGE_BACKEND`。
- 看图必须带登录态访问 `GET /media/{id}`，且照片属于当前家庭。
- `kind=child`：必须有 `taken_on`。家里正好一个孩子时可以省略 `child_id`。
- `kind=self`：`child_id` 必须为空，否则 400。缺省拍摄日是 Asia/Shanghai 的上传日。

## 测试

```bash
pytest
```

`tests/conftest.py` 会设置 `AUTH_TEST_CODE=000000`、测试用 `SECRET_KEY` 和本地 `PHOTO_DIR`。覆盖权限矩阵、验证码限流、默认密钥拒绝启动，以及迁移补列、切到 S3 后旧本地图仍可看、跨家庭 `/media` 403、坏存储时 `/healthz` 仍为 200。S3 用 moto，不启 MinIO。

## 发布说明

见 [CHANGELOG.md](CHANGELOG.md) 和 [releases/v2026.10.24.3.md](releases/v2026.10.24.3.md)。
