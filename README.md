# kids时光机

家庭相册的主线最小闭环（`v2026.10.23.1`）：登录、一个家庭、邀请、孩子时光线、孩子照片、家人本人相册、三档权限。

技术依据是 r2 修订稿（SHA256 `7206926873b45c4fae3117d6f868e01b74df0329ffcf85cab9c663c4b0a9f503`）。本版是 Python 从零实现，不接旧的 kids-album，也不做日志、视频、评论、真实发信、公开直链、发现、AI 或导出界面。

## 运行

需要 Python 3.12+。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/migrate.py
export AUTH_TEST_CODE=000000
export SECRET_KEY=dev-secret-change-me
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

`uvicorn` 启动时也会 `create_all`，所以漏跑迁移脚本仍能起服务。`scripts/migrate.py` 是首版的显式建表入口，重复执行是安全的。

数据文件：

- SQLite：默认 `sqlite:///./data/app.db`（可用 `DATABASE_URL` 改文件位置）
- 照片：`data/photos/`（可用 `PHOTO_DIR` 改位置）

这两处都不进 git。

## 登录

本版不发真实邮件。流程是：

1. 填写邮箱，请求验证码（服务端会记下一条短期验证码）。
2. 若设置了 `AUTH_TEST_CODE`，该值可以直接通过校验。开发默认是 `000000`。
3. 生产环境把 `AUTH_TEST_CODE` 设成空字符串即可关掉旁路。关掉之后，验证码只在 `auth_codes` 表里，需要自行读取后交给用户。

登录后若还没有姓名，页面会停在「完善资料」，填完才能进时光线。接口本身不拦时间线读取。

## 权限

| 档位 | 能做的事 |
|---|---|
| `admin` 家长管理员 | 邀请、改档、加孩子、上传 |
| `uploader` 可上传 | 上传孩子照片和本人照片 |
| `viewer` 只读 | 只看。页面不渲染「＋」 |

一个用户最多一个家庭。已经有家庭时，再创建或接受邀请会返回 400。

家庭里最后一位家长管理员不能被降级。

非成员访问家庭、孩子、时光线、成员、媒体、本人相册时返回 403，正文只有「没有访问权限」，不带家庭名、孩子名或成员列表。

## 照片

- 只接受 jpeg / png / webp，单张不超过 10MB。
- 文件名是随机 UUID，不提供公开直链。看图必须带登录态访问 `GET /media/{id}`。
- `kind=child`：必须有 `taken_on`。`child_id` 必填；家里正好一个孩子时可以省略，服务端会写成这个孩子。缺了或日期非法都是 400。
- `kind=self`：`child_id` 必须为空。若请求里带了非空 `child_id`，返回 400（不静默清空）。`taken_on` 缺省为 Asia/Shanghai 的上传日，也可以自己指定。本人照片不会出现在任何孩子时光线里。
- 关系字段是 `Membership.relation_label`，家庭内展示，不绑定 `child_id`。用 `PATCH /api/me` 修改。

## 测试

```bash
pytest
```

覆盖权限矩阵、最后一位管理员、本人照片不进孩子时光线、媒体 403、403 不泄密、孩子照片缺 `taken_on` / `child_id` 返回 400、第二家庭 400、`PATCH /api/me`。

## 发布与回退

见 [CHANGELOG.md](CHANGELOG.md) 和 [releases/v2026.10.23.1.md](releases/v2026.10.23.1.md)。
