# 时光机（family_album）

家庭成长相册：以孩子为主，也收进家人分享的照片。当前版本 `v2026.10.23.1`。

## 本版能做

- **家庭成员与关系**。一人一个家庭。成员填写姓名（1–40 字），并可写家庭内关系（如爸爸、外婆）。关系记在成员上，在家人列表里展示，不绑定某个孩子，用 `PATCH /api/me` 修改。家长管理员可以邀请家人（默认 7 天有效）；接受邀请须先用受邀邮箱登录。已经有家庭时，再创建或接受邀请会返回 400。
- **三档权限**。`admin` 家长管理员可以邀请、改档、加孩子、上传；`uploader` 可上传孩子照片和本人照片，不能改别人的权限；`viewer` 只读，页面不渲染「＋」。家庭里最后一位家长管理员不能被降级。
- **孩子照与本人照，按拍摄日展示**。孩子照片必须有拍摄日，按天出现在该孩子的时光线，并标出当时几岁几个月（未满月为「不足1个月」）。家人本人照片按拍摄日从新到旧排在本人相册，单张页展示日期；缺省拍摄日是 Asia/Shanghai 的上传日。本人照片不会出现在任何孩子的时光线里。

本版不做：日志、视频、评论、真实发信、公开直链、发现、AI、导出界面。

## 运行

需要 Python 3.12+。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/migrate.py
export AUTH_TEST_CODE=000000
export SECRET_KEY=dev-secret-change-me
export ALLOW_INSECURE_DEFAULTS=1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

上面三行环境变量只给本机开发。`AUTH_TEST_CODE` 不设置时默认是空字符串，测试旁路是关的，所以本地要显式写成 `000000`。`SECRET_KEY` 若仍是 `dev-secret-change-me`，进程会在启动时直接拒绝，除非同时设置 `ALLOW_INSECURE_DEFAULTS=1`。

`uvicorn` 启动时也会建表，漏跑 `scripts/migrate.py` 仍能起服务。该脚本是显式建表入口，重复执行是安全的。

### 生产

生产不要设置 `ALLOW_INSECURE_DEFAULTS`。上线前确认这三项：

- `AUTH_TEST_CODE` 为空，或者不设置。测试旁路必须关闭。
- `SECRET_KEY` 换成高熵随机串，不能继续用 `dev-secret-change-me`。
- 站点走 HTTPS 时设置 `COOKIE_SECURE=1`（也接受 `true` / `yes`）。会话 cookie 会带上 Secure。本地用 `http://127.0.0.1` 时不要开，否则浏览器不会保存登录态。默认是关的。

```bash
export AUTH_TEST_CODE=
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export COOKIE_SECURE=1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

限流按直接连上来的客户端地址计算，不读取 `X-Forwarded-For`。前面若有反向代理，同一出口会共用 IP 额度。

数据文件：

- SQLite：默认 `sqlite:///./data/app.db`（可用 `DATABASE_URL` 改文件位置）
- 照片：`data/photos/`（可用 `PHOTO_DIR` 改位置）

这两处都不进 git。

## 登录

本版不发真实邮件。流程是：

1. 填写邮箱，请求验证码。服务端生成 6 位数字，只把 HMAC-SHA256（密钥是 `SECRET_KEY`）写入 `auth_codes`，不存明文，响应里也不返回验证码。默认 10 分钟有效。
2. 若设置了 `AUTH_TEST_CODE`，必须先请求过验证码，该值才能通过校验。本地开发设为 `000000`。不设置或设成空字符串则旁路关闭。
3. 同一邮箱两次获取验证码至少间隔 60 秒。同一 IP 每小时最多 20 次、每天最多 50 次。被拒绝的请求不会换掉已经记下的验证码。超出时返回 429，正文说明要等多久或已达上限。
4. 同一邮箱连续验错 5 次后，在验证码有效期内（默认 600 秒，从第一次失败算起）会拒绝继续校验。同一 IP 累计失败 30 次同样拒绝。返回 429。验证成功，或该邮箱重新成功获取验证码，会清掉这个邮箱的失败次数。IP 的失败次数要等窗口过去，重新获取验证码不会把它清掉。
5. 新的验证码会使该邮箱上一条验证码失效。

关闭旁路之后，不能从数据库读出明文验证码交给用户。本版没有 SMTP。

登录后若还没有姓名，页面会停在「完善资料」，填完才能进时光线。接口本身不拦时间线读取。

## 权限

| 档位 | 能做的事 |
|---|---|
| `admin` 家长管理员 | 邀请、改档、加孩子、上传孩子照片和本人照片 |
| `uploader` 可上传 | 上传孩子照片和本人照片，不能改别人的权限 |
| `viewer` 只读 | 只看。页面不渲染「＋」 |

一个用户最多一个家庭。已经有家庭时，再创建或接受邀请会返回 400。

家庭里最后一位家长管理员不能被降级。

非成员访问家庭、孩子、时光线、成员、媒体、本人相册时返回 403，正文只有「没有访问权限」，不带家庭名、孩子名或成员列表。

## 照片

- 只接受 jpeg / png / webp，单张不超过 10MB。
- 文件名是随机 UUID，不提供公开直链。看图必须带登录态访问 `GET /media/{id}`，且照片属于当前家庭。
- `kind=child`：必须有 `taken_on`。`child_id` 必填；家里正好一个孩子时可以省略，服务端会写成这个孩子。缺了或日期非法都是 400。照片按拍摄日分组出现在孩子时光线。
- `kind=self`：`child_id` 必须为空。若请求里带了非空 `child_id`，返回 400（不静默清空）。`taken_on` 缺省为 Asia/Shanghai 的上传日，也可以自己指定。本人照片按拍摄日排在本人相册，不会出现在任何孩子时光线里。

## 测试

```bash
pytest
```

`tests/conftest.py` 会自行设置 `AUTH_TEST_CODE=000000` 和测试用 `SECRET_KEY`。覆盖权限矩阵、最后一位管理员、本人照片不进孩子时光线、媒体 403、403 不泄密、孩子照片缺 `taken_on` / `child_id` 返回 400、第二家庭 400、`PATCH /api/me`，以及验证码旁路关闭、请求限流、验错锁定、默认密钥拒绝启动。

## 发布说明

见 [CHANGELOG.md](CHANGELOG.md) 和 [releases/v2026.10.23.1.md](releases/v2026.10.23.1.md)。
