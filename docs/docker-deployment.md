# aiyunying Docker 部署手册

## 1. 部署结构

本手册默认使用 Linux 服务器和现有远程 PostgreSQL。Docker Compose 启动一个应用容器：内含 FastAPI、LangGraph、Node.js 签名运行时、中文字体，以及编译后的 React 前端。浏览器访问同一个 8000 端口，前端通过 `/api` 调用后端，无需启动 Vite。

```text
浏览器 → 服务器:8000 → 应用容器
                       ├─ PostgreSQL：保存账号、选题、文案、卡片信息
                       ├─ 文案网关：选题、文案、首次卡片摘要与提示词
                       ├─ 火山方舟：Seedream 生图和重绘
                       └─ Docker 持久卷：图片、上传文件、导出资源
```

| 文件 | 用途 |
| --- | --- |
| `Dockerfile` | 分阶段构建前端、签名依赖和 Python 运行镜像 |
| `docker-compose.yml` | 端口、持久卷、环境变量、健康检查与日志轮转 |
| `.env.example` | 部署配置模板，真实密钥只填服务器 `.env` |
| `.dockerignore` | 排除本机虚拟环境、密钥、日志和生成图片 |
| `deploy/nginx.conf.example` | 可选的域名反向代理配置 |

应用以 UID/GID 10001 运行，使用一个 Uvicorn worker，避免启动时并发迁移或重复定时任务。启动时自动执行 Alembic 迁移；**数据库必须预先存在**，程序不负责创建 PostgreSQL 数据库。

## 2. 服务器准备

建议 Demo 从 2 核 CPU、4 GB 内存和 10 GB 可用磁盘起步；这些是部署预算建议，并非经过压测的容量指标。镜像首次构建会下载 Node、Python 和系统依赖，服务器需能访问镜像仓库、npm、PyPI，以及运行时的数据库、网关和火山接口。

安装 Docker Engine、Buildx 和 Compose 插件。Ubuntu 安装步骤以 [Docker 官方手册](https://docs.docker.com/engine/install/ubuntu/) 为准，安装后确认：

```bash
docker version
docker compose version
docker buildx version
```

以下命令假设当前用户具有 Docker 权限；否则在 Docker 命令前加 `sudo`。服务器安全组允许浏览器访问应用端口 8000；数据库侧允许该服务器访问 5432。使用域名反代时仅开放 80/443，并将应用绑定到本机地址。

## 3. 获取代码与配置

```bash
mkdir -p ~/apps
cd ~/apps
git clone --branch master git@github.com:superno188462/aiyunying.git
cd aiyunying
cp .env.example .env
chmod 600 .env
nano .env
```

SSH 克隆需要服务器 SSH Key 有仓库读取权限。也可以使用 HTTPS 地址克隆，私有仓库需配置相应凭据。

填写以下配置；示例中的密码与 Key 均为占位符：

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@119.45.48.180:5432/aiyunying
SECRET_KEY=YOUR_RANDOM_SECRET
GATEWAY_BASE_URL=http://119.45.48.180:8080/gateway
GATEWAY_API_KEY=YOUR_GATEWAY_KEY
GATEWAY_MODEL=YOUR_TEXT_MODEL
ARK_API_KEY=YOUR_ARK_KEY
ARK_IMAGE_URL=https://ark.cn-beijing.volces.com/api/plan/v3/images/generations
ARK_IMAGE_MODEL=doubao-seedream-5-0-pro-260628
ARK_IMAGE_SPEED=fast
ARK_IMAGE_SIZE=1K
APP_BIND_HOST=0.0.0.0
APP_PORT=8000
IMAGE_TAG=local
SCHEDULER_ENABLED=false
```

| 参数 | 配置要点 |
| --- | --- |
| `DATABASE_URL` | 填实际数据库名；密码中的 `@`、`#`、`%` 等字符须做 URL 编码。也接受 `postgresql+asyncpg://`，内部统一转为同步 psycopg 地址 |
| `SECRET_KEY` | 新部署生成固定随机值；迁移已有数据时保留原值，否则会影响登录令牌与已保存的加密凭据 |
| `FERNET_KEY` | 可选；空值时由 `SECRET_KEY` 派生。已有部署配置过此项时必须一起迁移 |
| `GATEWAY_*` | 用于文本生成，与火山 Key 独立 |
| `ARK_IMAGE_URL` | 当前使用 Agent Plan 专属 `/api/plan/v3/` 接口；普通后付费 Key 使用 `/api/v3/`，应与 Key 类型匹配 |
| `ARK_IMAGE_SPEED` | Demo 用 `fast`，质量优先用 `standard` |
| `ARK_IMAGE_SIZE` | Demo 用 `1K`，定稿可用 `2K`；快速模式也不保证 10 秒完成，当前单张实测约 31–36 秒 |
| `SCHEDULER_ENABLED` | 内容策划不依赖定时器；需要已有自动发布/监控定时功能时才改为 `true` |

新部署可以用镜像生成随机密钥，再复制输出到 `.env`：

```bash
docker run --rm python:3.11-slim-bookworm python -c 'import secrets; print(secrets.token_hex(32))'
```

如数据库尚不存在，由数据库管理员在 PostgreSQL 中执行：

```sql
CREATE DATABASE aiyunying;
```

应用连接用户需要对该数据库拥有建表及迁移权限。数据库地址不能写容器里的 `127.0.0.1` 来指代宿主机数据库；当前远程数据库按实际 IP 填写即可。

## 4. 构建并启动

```bash
# 仅校验配置，不输出含密钥的完整 Compose 内容
docker compose config -q

# 用 Git 提交号标记镜像，便于后续回退
export IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose build --pull app
docker compose up -d --wait --wait-timeout 180 app

docker compose ps
docker compose logs --tail=100 app
curl -f http://127.0.0.1:8000/api/health
```

首次镜像构建可能耗时较长。启动时数据库迁移失败会导致应用无法启动，先查看容器日志；不要在同一数据库上同时启动多个首次迁移实例。

健康接口预期返回：

```json
{"status":"ok","service":"spider-xhs"}
```

浏览器访问 `http://服务器IP:8000`，API 文档为 `http://服务器IP:8000/docs`。注册平台账号后进入“内容策划”，输入主题即可开始；小红书账号绑定和参考笔记均不是主题策划的前提。首次生成图片会先调用文案网关提炼卡片，再由火山生图；已有图片重绘直接调用火山。

健康检查确认应用可以响应，不代表远程网关、火山额度或数据库后续连接始终可用。可先检查页面、登录和主题保存，再按需发起实际 AI 请求；生图会消耗对应套餐额度或产生费用。

## 5. 修改配置与日常管理

修改 `.env` 后需要重新创建容器，单纯 `restart` 不会加载 Compose 中的新环境变量：

```bash
docker compose up -d --force-recreate --wait app
docker compose logs -f --tail=100 app
```

若启动时使用了提交号镜像标签，后续管理命令需保持相同的 `IMAGE_TAG`，例如重新登录 SSH 后执行 `export IMAGE_TAG=$(git rev-parse --short HEAD)`。也可将当前标签写到 `.env` 的 `IMAGE_TAG` 项，避免 shell 会话丢失。

```bash
docker compose restart app    # 配置未变时重启
docker compose stop app       # 停止应用
docker compose start app      # 启动已有容器
docker compose down           # 移除容器，保留数据卷
```

数据库保存在远程 PostgreSQL；文件保存在命名卷 `aiyunying_app_data` 和 `aiyunying_app_storage` 中。**不要使用 `docker compose down -v` 清理部署**，它会删除这些文件卷。Compose 项目名固定为 `aiyunying`；如果改项目名，应同步处理原数据卷。

服务器不会自动带上 Windows 本机的图片和 `.env`。如果共用原数据库，里面的卡片记录仍在，但原图必须单独迁移，否则页面会提示图片文件缺失。迁移时保留目录结构，尤其是 `content_cards/.batches/`。

## 6. 备份与恢复

完整备份包含：PostgreSQL 数据、两个文件卷、`.env` 中的密钥。先等待正在生成的任务结束并停止应用，减少数据与文件不一致。

```bash
mkdir -p backups
chmod 700 backups
stamp=$(date +%Y%m%d-%H%M%S)
docker compose stop app
docker compose run --rm --no-deps -T app tar -czf - -C /app data backend/app/storage > "backups/assets-$stamp.tar.gz"
cp .env "backups/env-$stamp"
chmod 600 "backups/env-$stamp"
```

使用与 PostgreSQL 服务端版本兼容的 `pg_dump` 客户端备份，密码通过交互输入：

```bash
pg_dump -h 119.45.48.180 -p 5432 -U postgres -d aiyunying -Fc -f "backups/database-$stamp.dump"
docker compose start app
```

数据库恢复到预先创建的空数据库，应用保持停止：

```bash
pg_restore -h YOUR_DB_HOST -p 5432 -U YOUR_DB_USER -d YOUR_EMPTY_DB backups/database-TIMESTAMP.dump
docker compose run --rm --no-deps -T app tar -xzf - -C /app < backups/assets-TIMESTAMP.tar.gz
# 将备份的密钥恢复到 .env，DATABASE_URL 指向恢复后的数据库
docker compose up -d --force-recreate --wait app
```

从 Windows 迁移文件时，可将本机 `backend/app/storage` 打包为相同目录结构的 tar.gz，上传到服务器后用上面的 tar 恢复命令导入持久卷。数据库仍使用原远程地址时不需要重复导入数据库。

## 7. 更新与回退

更新前按上一节备份，并记录旧镜像标签：

```bash
docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q app)"
git pull --ff-only origin master
export IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose build --pull app
docker compose up -d --wait --wait-timeout 180 app
docker compose logs --tail=100 app
```

构建失败时旧容器通常仍继续运行，不要先删除它。保留旧镜像，代码回退可选择旧标签启动：

```bash
IMAGE_TAG=OLD_COMMIT_TAG docker compose up -d --no-build --force-recreate --wait app
```

镜像回退不会回退数据库结构；新迁移与旧版本不兼容时，需要同时恢复更新前的数据库和文件备份。运行任务期间避免部署更新，未完成的同步生图请求不会在重启后自动续跑。

## 8. 可选：域名和 Nginx

将 `.env` 的 `APP_BIND_HOST` 改为 `127.0.0.1`，重新创建容器。宿主机安装 Nginx 后，复制 `deploy/nginx.conf.example` 到 `/etc/nginx/conf.d/aiyunying.conf`，修改域名与代理端口，然后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

模板只包含 HTTP；对外使用真实账号与密钥前配置域名 HTTPS 证书。模板将代理读取超时设为 900 秒，以覆盖逐张生图等待；若使用宝塔或其他反代，也要调整相应超时。

## 9. 常见问题

| 现象 | 排查方式 |
| --- | --- |
| 连接 Docker daemon 失败 | 确认 Docker 服务启动、当前用户权限和 Docker context |
| 8000 端口冲突 | 修改 `APP_PORT`，重新创建容器，同时调整安全组和反代端口 |
| 容器持续重启 | 查看日志中的数据库连接或 Alembic 迁移错误；检查数据库名、密码、访问权限 |
| 首页 404 或没有前端 | 查看构建是否成功；Compose 固定启用 `FRONTEND_SERVE_STATIC=true`，检查是否使用了本项目 Compose |
| PostgreSQL 连接失败 | 核实服务器到数据库 5432 的连通性、数据库防火墙及 `pg_hba.conf` |
| AI 网关返回 502 | 排查文本网关；首次图片摘要提炼也依赖它，重绘直接调用火山 |
| 火山鉴权失败或额度不足 | 核实 Key 类型、图片接口地址、模型权限和套餐额度 |
| 页面等待或反代 504 | 查看 `Seedream complete/timeout` 日志；核实代理读取超时，当前快速模式仍可能超过 10 秒 |
| 图片文件不存在 | 核实存储卷，迁移本机的 `backend/app/storage/content_cards`，保留 `.batches` 子目录 |
| 保存账号后无法解密 | 恢复原 `SECRET_KEY` 与原 `FERNET_KEY`，不要每次部署重新生成 |
| 构建无法下载依赖 | 检查服务器 DNS、镜像仓库、npm/PyPI 出网；配置可访问的镜像服务后重试 |

本项目基于第三方开源 Python 项目，镜像沿用 `requirements.txt` 安装依赖。当前 Python 依赖未完全锁定精确版本，因此保留已验收的镜像标签用于稳定运行与回退。
