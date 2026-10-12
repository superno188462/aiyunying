# 内容策划工作台

本模块集成在原项目内。登录后进入“小红书 → 内容策划”，输入主题并选择参考来源，依次完成热门参考、AI 选题、文案审核、图片生成和导出。

参考来源有两种：

- **主题策划（参考可选，免绑定）**：只输入主题即可开始 AI 选题；也可添加 1–5 条参考笔记的标题与内容，可选填原文链接及点赞、收藏、评论数。链接仅作为来源记录，不会自动读取网页内容。没有参考时，AI 只提出创作方向，不声称已验证爆款热度。
- **账号自动获取**：选择已绑定的小红书 PC 账号，由现有搜索接口采集笔记并按互动数据排序。

## 服务边界

- 前端：现有 React + Ant Design 页面，通过共享 `contentWorkflowApi` 调用后端。
- 后端：FastAPI 负责账号权限、流程阶段和并发版本校验；SQLAlchemy/Alembic 持久化任务、参考笔记、选题、每次文案版本和图片摘要。
- AI：LangGraph 将选题、长文、卡片提炼分成三个节点。人工选择和确认由后端阶段状态控制，每次确认后才允许下一个节点运行。
- 模型：服务端通过网关 `/gateway/v1/chat/completions` 生成选题、长文、卡片摘要和每张图片的视觉提示词；火山方舟 Seedream 5.0 Pro 生成插画背景，Pillow 将已审核的中文标题和摘要叠加成 1080×1440 PNG。直接生图需要独立的 `ARK_API_KEY`，不会复用文案网关密钥。
- 数据：`DATABASE_URL` 使用远端 PostgreSQL 的独立 `aiyunying` 数据库；现有同步 SQLAlchemy 自动将 `postgresql+asyncpg://` 驱动标识适配为 `postgresql+psycopg://`。PNG 保存在 `backend/app/storage/content_cards`，Docker Compose 挂载到宿主机。

## 启动

1. 当前本机已创建 Git 忽略的 `.env`，配置远端 PostgreSQL 和随机 `SECRET_KEY`。文案需要 `GATEWAY_API_KEY` 和 `GATEWAY_MODEL`；Seedream 生图另需火山方舟的 `ARK_API_KEY`。默认生图模型 ID 为 `doubao-seedream-5-0-pro-260628`，可用 `ARK_IMAGE_MODEL` 覆盖。部署到其他机器时以 `.env.example` 为模板创建各自的 `.env`。
2. 启动 Docker Desktop 后，在项目根目录运行 `docker compose up --build -d`。首次启动会执行 Alembic 迁移；使用本机 Python 启动也连接同一远端数据库。
3. 打开 `http://localhost:8000`，注册或登录原项目账号。手动参考入口可直接使用；只有账号自动获取入口需要在“账号矩阵”绑定小红书 PC 账号。

现有开源项目继续使用自己的 `requirements.txt` 和 Dockerfile。新增 Python 依赖是 `langgraph`、`psycopg[binary]`；Docker 镜像同时安装中文字体用于准确渲染卡片文字。

## 审核和导出

- 自动采集的热门笔记依互动数据排序；手动参考按录入顺序保存，也允许完全不填参考。生成时只把必要字段传给模型，提示词要求原创表达。手动填写的互动数据来自用户，系统不核验真实性；没有参考时不做热度判断。
- 选择子选题后才能生成长文；可保存人工修改、按意见重写、查看历史版本。确认文案后才能生成图片。
- 图片先由 Seedream 按每张卡片的视觉提示词生成无字插画，再叠加经过审核的中文文字。可逐张改文字并重新排版，也可修改视觉提示词只重绘一张背景；重绘会再次调用图片接口并产生费用。发布包包含 `wechat.md`、`xiaohongshu.txt`、`cards/*.png` 和 `sources.json`。
- 公众号和小红书的实际发布由运营者在对应平台完成。现有项目的小红书发布中心可以在后续接入这一批 PNG。

## API 与错误

接口在 `/api/content-workflows`，详情见 FastAPI `/docs`。每次变更发送详情中的 `version`；旧版本返回 409 和 `STALE_VERSION`。阶段越级返回 `INVALID_STAGE`。模型或搜索不可用时保留已经保存的任务状态，页面可以刷新和重试。

## 已知限制

- 主题策划模式不需要小红书账号或参考笔记；AI 选题和文案需要网关项目 Key，Seedream 图片需要单独的火山方舟 Key。未配置 `ARK_API_KEY` 时图片生成会给出明确错误，已审核文案仍会保留。
- Seedream 原始插画与叠加文字后的成品保存在服务端；已有的旧版文字卡片不会自动收费重做，点击“重新生成全部图片”后才会调用 Seedream。
- 首次生图先由文案网关提炼卡片摘要与视觉提示词，再调用火山方舟；已有卡片的全部重绘和单张背景重绘直接调用火山方舟，复用已保存的文字与提示词。
- Demo 默认使用 `ARK_IMAGE_SPEED=fast`、`ARK_IMAGE_SIZE=1K`，启用 Seedream Pro 快速模式并降低生成分辨率；接口返回 JPEG 以减少传输量，最终卡片仍输出 PNG。定稿可改为 `ARK_IMAGE_SPEED=standard`、`ARK_IMAGE_SIZE=2K`。实际生成时延由火山服务与网络共同决定，不保证 10 秒完成。
- Docker Desktop 只用于容器化运行应用；数据库位于远端 PostgreSQL，不再启动本地 PostgreSQL 容器。
