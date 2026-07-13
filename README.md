# 橡胶工厂 ERP

面向橡胶制品工厂的生产、品检与模具管理系统。系统统一管理生产订单与每日产量、品检出货、退货返工、员工绩效依据，以及模具位置、状态和流转历史。

## 技术栈

- 前端：React 19、TypeScript、Vite、Ant Design、TanStack Query
- 后端：Python 3.11、Django 5.2 LTS、Django REST Framework
- 数据库：SQLite（WAL、单后端实例）
- 部署：Docker Compose、Nginx、Gunicorn

## 本地开发

开发工具统一放在 `D:\develop`：

- Python：`D:\develop\python311`
- Python虚拟环境：`D:\develop\venvs\erp`
- Node.js：`D:\develop\node22`
- Git：`D:\develop\git`
- 下载及包缓存：`D:\develop\cache`

首次准备环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1
```

该脚本只安装或修复 Python 3.11、Node.js 22、PortableGit及项目依赖，不会检测、安装或启动Docker。

首次初始化共享账号。请把示例密码换成实际密码：

```powershell
cd backend
D:\develop\venvs\erp\Scripts\python.exe manage.py migrate
D:\develop\venvs\erp\Scripts\python.exe manage.py init_erp --username erpadmin --password "请替换为实际密码"
cd ..
```

以后通过一个命令启动前后端；该命令会自动执行迁移和幂等初始化：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000
- API文档：http://127.0.0.1:8000/api/docs/

## GitHub Actions与GHCR镜像

仓库中的 GitHub Actions 会在 Pull Request 中执行后端和前端测试；推送到 `main`、推送 `v*` 版本标签或手动运行工作流时，还会构建以下多架构镜像并推送到 GitHub Container Registry：

```text
ghcr.io/<GitHub账号>/<仓库名>-backend
ghcr.io/<GitHub账号>/<仓库名>-web
```

镜像同时支持 `linux/amd64` 和 `linux/arm64`。后端镜像也供 `backup` 服务复用。默认分支会生成 `latest`、分支名和 `sha-*` 标签；`v1.0.0` 之类的Git标签还会生成对应版本标签。

首次上传前检查待提交内容：

```powershell
git status --short
git add .
git status --short
git commit -m "Initial mold ERP"
git branch -M main
git remote add origin https://github.com/<GitHub账号>/<仓库名>.git
git push -u origin main
```

`.gitignore` 已排除工作区根目录中的Excel业务资料和预览图片，`.dockerignore` 也不会把这些文件传入Docker构建上下文。执行 `git add .` 后仍应检查一次列表，确认没有准备上传不应公开的资料。

Actions 使用仓库自带的 `GITHUB_TOKEN` 推送镜像，不需要另建发布Token，但工作流权限必须允许 `packages: write`。GHCR包可能默认是私有的：

- 公开镜像：在GitHub包设置中将可见性改为Public，服务器无需登录即可拉取。
- 私有镜像：服务器使用具有 `read:packages` 权限的访问令牌登录，令牌不要写进项目的 `.env`。

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <GitHub账号> --password-stdin
```

## Docker Compose部署

本项目不要求在当前开发电脑安装Docker。推荐由GitHub Actions构建镜像，然后在安装了 Docker Engine 和 Docker Compose v2 的Linux服务器上仅执行拉取和启动。

在服务器克隆仓库后：

```bash
cp .env.example .env
mkdir -p runtime/data runtime/media runtime/backups
```

必须编辑 `.env`：

- `GHCR_BACKEND_IMAGE`和`GHCR_WEB_IMAGE`默认已指向本仓库发布的`latest`镜像；如果使用Fork仓库或固定版本，再改为对应镜像地址或版本标签。
- 将 `DJANGO_SECRET_KEY` 换成随机密钥，可用 `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` 生成。
- 将 `DJANGO_SUPERUSER_PASSWORD` 换成强密码。
- 将 `DJANGO_ALLOWED_HOSTS` 改成实际域名或服务器地址，多个值用英文逗号分隔。
- 将 `DJANGO_CSRF_TRUSTED_ORIGINS` 改成完整访问来源，例如 `https://erp.example.com`。
- 外层已启用HTTPS时，将 `DJANGO_SECURE_COOKIES` 改成 `1`。

部署只需要仓库根目录的一个 `compose.yaml` 文件。该文件直接拉取 `.env` 中配置的GHCR镜像，不在服务器上构建源码：

```bash
docker compose config
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

默认HTTP入口为 `http://服务器地址:8080`。Compose本身不签发HTTPS证书；公网部署应由服务器上的反向代理提供域名和HTTPS，并保留 `Host`、`X-Forwarded-Proto` 请求头。

持久化目录：

- `runtime/data`：SQLite数据库及WAL文件
- `runtime/media`：模具图片
- `runtime/backups`：备份压缩包

后端固定为一个Gunicorn进程和多个线程，不要扩容多个后端容器。SQLite不适合多实例并发写入。

### 账号维护

首次启动会按 `.env` 创建共享账号。以后修改 `.env` 中的密码不会自动覆盖已有密码，需要显式重置：

```bash
docker compose exec backend \
  python manage.py init_erp --reset-password
```

### 备份与恢复

`backup` 服务每天按 `Asia/Shanghai` 时区在02:00执行SQLite在线一致性备份，并将媒体文件放入同一个ZIP包。默认保留最近30份，可通过 `BACKUP_RETENTION_COUNT` 调整。

立即执行一次在线备份：

```bash
docker compose exec backend \
  python manage.py backup_erp
```

恢复会替换当前数据库和媒体目录。先另行复制当前 `runtime` 目录，再执行：

```bash
docker compose stop web backup backend
docker compose run --rm --no-deps backend \
  python manage.py backup_erp --restore /app/backups/备份文件名.zip --force
docker compose up -d
```

### 更新与回滚

更新 `latest` 镜像：

```bash
docker compose exec backend \
  python manage.py backup_erp
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

生产环境更推荐在 `.env` 中固定 `v1.0.0` 或 `sha-*` 标签。需要回滚时改回旧标签，再重复 `pull` 和 `up`。容器入口会自动执行数据库迁移、静态文件收集及幂等 `init_erp`，更新前仍应手动备份。

## 初始货架

- J01：6层，左右两区默认各2位，可分别切换为3位。
- J02：8层，左右两区默认各2位，可分别切换为3位。
- J03、J04：6层整区，默认2位，可切换为3位。
- J05：4层，左右两区分别支持2/3/4位。
- J06：下部6层由左、中、右三个区域组成，默认按`2+2+2`摆放，小模具时可切换为`3+3+3`；中区向上增加3层，左右两侧的第7至9层为杂物区，禁止放置模具。
- J07：保留为待配置空架。

J01至J06的每个可用区域都支持独立切换容量和叠放显示。关闭叠放时只显示S1下层，开启后才显示S2上叠层；区域内仍有模具时不能切换容量，上叠层仍有模具时不能关闭叠放。J06上部左右杂物区为固定禁放区，不能开启容量或叠放。

## 前端生产管理

- 固定三组双联机台，共6台，孔位即机台：第一组为1号、2号，第二组为3号、4号，第三组为5号、6号；同组两台设备连在一起。
- 生产看板显示订单、规格、上模时间、预计换模时间和实时倒计时；临近换模变黄，超时变红。
- 每张生产订单可按天、按作业员补录生产模数，完工后统一登记良品、不良、材料、人工、能耗及其他成本。
- 系统自动计算计划模数、预计换模时间、实际工时、完成进度、欠模数、收入、成本、利润和工时效率，并保留结算修订记录。
- “生产订单统计表”采用“每个工作表一张订单卡”的格式；机台编号统一填写全局编号 `1`–`6`，旧台账中的 `A01`、`A02`、`B01`、`B02`、`C01`、`C02` 可兼容导入；上传后先预检，整批无错误才会事务化写入数据库。
- 生产Excel历史导入只建立生产记录，不会改变模具台账中的当前库位或状态。

## 品检出货与退货返工

- 员工档案使用唯一工号，分别标记品检、返工或兼任岗位，停用员工不会丢失历史绩效记录。
- 每批出货记录出货单号、日期、订单批次、责任品检员、质检数量、合格数量、不良数量和实际出货数量。
- 每次退货返工关联原出货单，分别记录责任品检员和实际返工处理员工，避免把质量责任与返工工作量混在一起。
- 系统自动校验“质检数＝合格数＋不良数”“出货数不超过合格数”“返工合格＋报废不超过返工数”，并限制累计退货数量不得超过原出货数量。
- 页面按日期区间展示每日趋势、订单批次统计和员工绩效依据，包括质检量、一次合格率、责任退货量、返工处理量和返工通过率。
- 同一出货单关联的返工事件超过3次时显示红色预警；业务记录保留审计，不开放直接删除。

## 检查与测试

不调用Docker的部署文件静态检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-deployment.ps1
```

项目测试：

```powershell
cd backend
D:\develop\venvs\erp\Scripts\python.exe manage.py test

cd ..\frontend
$env:PATH='D:\develop\node22;'+$env:PATH
D:\develop\node22\npm.cmd run lint
D:\develop\node22\npm.cmd run test
D:\develop\node22\npm.cmd run build
```

## 重要限制

- 系统目前仍使用共用登录账号，可记录责任员工和返工员工，但不能区分具体的系统录入经办人。
- 状态由人员手动更新，不连接设备自动判断。
- 当前不包含二维码、离线模式、设备自动采集和智能排程。
- Python 3.11预计在2027年10月结束安全维护，应在此前升级到仍受支持的Python版本。
