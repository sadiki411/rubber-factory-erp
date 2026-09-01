# 橡胶工厂 ERP

面向橡胶制品工厂的生产、品检与模具管理系统。系统统一管理产品规格、客户订单、胶料与流程卡、生产订单与每日产量、品检出货、退货返工、员工绩效依据，以及模具位置、状态和流转历史。

## 技术栈

- 前端：React 19、TypeScript、Vite、Ant Design、TanStack Query
- 后端：Python 3.11、Django 5.2 LTS、Django REST Framework
- 数据库：SQLite（WAL、单后端实例）
- 部署：Docker Compose、Nginx、Gunicorn
- 手机入口：原生Android应用“东橡生产助手” + 微信小程序预留入口

## 本地开发

开发工具统一放在 `D:\develop`：

- Python：`D:\develop\python311`
- Python虚拟环境：`D:\develop\venvs\erp`
- Node.js：`D:\develop\node22`
- Git：`D:\develop\git`
- 本地纸质账OCR：`D:\develop\tesseract`（Tesseract 5.4，`chi_sim+eng`）
- 下载及包缓存：`D:\develop\cache`

首次准备环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1
```

该脚本只安装或修复 Python 3.11、Node.js 22、PortableGit及项目依赖，不会检测、安装或启动Docker。本机若已在`D:\develop\tesseract`放置Tesseract，启动脚本会自动加入PATH；Docker后端镜像已固定安装Tesseract及简体中文语言包。

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

## Android应用

`android-app/` 提供面向OPPO等安卓手机的“东橡生产助手”APK。App直接连接 `https://erp.qvgro.com/`，网页部署后会自动使用最新功能；只有Android原生外壳变化时才需要重新发布APK。本机构建、正式签名、GitHub Release和ColorOS安装说明见 [android-app/README.md](android-app/README.md)。

## 微信小程序

`wechat-miniprogram/` 提供独立的微信小程序工程。小程序使用原生快捷首页展示工作台、模具、货架、订单、生产、品检、数据分析和产品规格入口，再通过固定白名单打开 `https://erp.qvgro.com` 的移动端页面；它不复制数据库，也不会在代码或URL中保存ERP密码和Session。

正式发布需要非个人主体小程序的真实 AppID、开发者权限、`qvgro.com` ICP备案，以及在微信公众平台把 `https://erp.qvgro.com` 配置为业务域名。仓库保留 `touristappid` 占位值，不提交AppSecret或上传私钥。完整配置与审核步骤见 [微信小程序说明](wechat-miniprogram/README.md)。

静态检查：

```powershell
D:\develop\venvs\erp\Scripts\python.exe scripts\check_miniprogram.py
```

GitHub Actions会独立验证小程序目录、固定HTTPS域名、页面完整性和敏感信息边界；网页功能更新后，小程序会直接加载新页面，通常不需要重新提交审核。

## Docker Compose部署

本项目不要求在当前开发电脑安装Docker。推荐由GitHub Actions构建镜像，然后在安装了 Docker Engine 和 Docker Compose v2 的Linux服务器上仅执行拉取和启动。

服务器上只需保存一个 `compose.yaml`，无需克隆源码，也不强制要求 `.env`。在该文件所在目录直接执行：

```bash
docker compose config
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

`compose.yaml` 还包含一个仅更新带有 Watchtower 标签的 ERP 服务的自动更新容器。首次在服务器执行上述命令后，它每5分钟检查一次公开 GHCR 镜像；发现新镜像会先拉取并滚动重启，旧镜像不自动清理，方便按镜像摘要回滚。Watchtower 需要访问 Docker socket，等同于该服务器的管理权限，只应在受信任的专用主机上运行。如果生产环境不允许此权限，可删除 `watchtower` 服务，改用仓库中的 SSH 部署工作流。

默认直接拉取本仓库的两个公开 `latest` 镜像，运行时自动创建 `runtime/data`、`runtime/media`、`runtime/backups`。首次启动若未提供 `DJANGO_SECRET_KEY`，后端会生成安全随机密钥并保存到 `runtime/data/.django-secret-key`，以后重启继续使用；若未提供共享账号密码，启动日志会一次性显示系统生成的初始密码：

```bash
docker compose logs backend
```

如需固定镜像版本、域名、HTTPS Cookie或初始账号，可以在执行Compose前设置同名环境变量；也可自愿放置Docker Compose会自动读取的 `.env`，但它不是启动所必需的。可配置项参见仓库中的 `.env.example`：

- `GHCR_BACKEND_IMAGE`、`GHCR_WEB_IMAGE`：Fork仓库镜像或固定的`v*`、`sha-*`版本。
- `DJANGO_ALLOWED_HOSTS`：实际域名或服务器地址；默认允许当前请求主机。
- `DJANGO_CSRF_TRUSTED_ORIGINS`：需要额外信任的完整来源，例如 `https://erp.example.com`。
- `DJANGO_SECURE_COOKIES=1`：外层已启用HTTPS时开启。
- `DJANGO_SECRET_KEY`、`DJANGO_SUPERUSER_PASSWORD`：可自行固定安全随机值；不要把真实密钥提交到Git。

默认HTTP入口为 `http://服务器地址:8080`。Compose本身不签发HTTPS证书；公网部署应由服务器上的反向代理提供域名和HTTPS，并保留 `Host`、`X-Forwarded-Proto` 请求头。

持久化目录：

- `runtime/data`：SQLite数据库及WAL文件
- `runtime/media`：模具图片
- `runtime/backups`：备份压缩包

后端固定为一个Gunicorn进程和多个线程，不要扩容多个后端容器。SQLite不适合多实例并发写入。

### 账号维护

首次启动创建共享账号，默认用户名为 `erpadmin`。未指定密码时，应从首次启动日志保存一次性初始密码。以后设置环境变量不会自动覆盖已有密码，需要显式重置：

```bash
DJANGO_SUPERUSER_PASSWORD='替换为新强密码' docker compose run --rm --no-deps backend \
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

更新 `latest` 镜像。后端容器检测到已有SQLite数据库时，会在执行迁移前自动创建一次一致性备份；如果备份失败，容器会停止启动，不会继续迁移。可通过 `BACKUP_BEFORE_MIGRATE=0` 关闭，但生产环境不建议关闭。

```bash
docker compose exec backend \
  python manage.py backup_erp
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

生产环境更推荐通过可选 `.env` 或服务器环境变量固定 `v1.0.0` 或 `sha-*` 标签。需要回滚时改回旧标签，再重复 `pull` 和 `up`。容器入口会自动执行数据库迁移、静态文件收集及幂等 `init_erp`；默认在每次迁移前先生成SQLite与媒体文件一致性备份，保留份数由 `BACKUP_RETENTION_COUNT` 控制。重大更新仍应在服务器核对新备份已生成后再继续。

## 初始货架

- J01：6层，左右两区默认各2位，可分别切换为3位。
- J02：8层，左右两区默认各2位，可分别切换为3位。
- J03、J04：6层整区，默认2位，可切换为3位。
- J05：4层，左右两区分别支持2/3/4位。
- J06：下部6层由左、中、右三个区域组成，默认按`2+2+2`摆放，小模具时可切换为`3+3+3`；中区向上增加3层，左右两侧的第7至9层为杂物区，禁止放置模具。
- J07：保留为待配置空架。

J01至J06的每个可用区域都支持独立切换容量和叠放显示。关闭叠放时只显示S1下层，开启后才显示S2上叠层；区域内仍有模具时不能切换容量，上叠层仍有模具时不能关闭叠放。J06上部左右杂物区为固定禁放区，不能开启容量或叠放。

## 产品规格与订单管理

- “产品规格资料”独立维护产品名称、客户产品编号、规格、材质、裁料参数、一次硫化、二烤、孔数和关联模具型号；同一模具型号可对应多条不同规格参数。工艺字段按原始文本保存，不会把单位、范围或特殊显示格式强制改成数字。工时由具体订单记录，不再从产品规格自动带入。
- “订单管理”按总表方式统一维护订单明细、下单日期、交期、数量、成型工时、是否生产、所需/已发胶料、流程卡、生产数量、出货日期和出货数量；支持按下单日期或交期升序、降序排列，未登记日期始终置底。客户发料明细会实时汇总，并显示未收到、未发够、已发够或超额到料。
- 支持产品规格记录表、内部订单总表、大厂生产工作联络单和客户发料清单四类 `.xlsx` 自动识别。上传后先预检，明确显示拟新增、更新或跳过的记录及错误警告；存在阻断错误时整批不写入数据库。
- 首次启用时先导入自有订单总表建立基准；以后直接上传客户工作联络单和发料清单即可增量更新同一张在线订单总表，不需要再手工抄录。
- 导入保留原始文件、原始单元格值、Excel显示文本和数字格式，兼容样式不规范但数据仍有效的工作簿。相同源文件重复上传会按源行跳过，不覆盖在线修正，也不会把订单表中的重复业务行擅自合并。
- 客户订单以“来源单位＋独立需求号＋项次”为稳定身份，同一订单的新版本会更新原记录而不会重复新增；旧总表没有项次时，仅在订单号、规格、材质、订单量和交期唯一命中时自动补齐。
- 客户发料以“来源单位＋独立需求号＋项次＋批号”为稳定身份，重复导入不会重复累计。能唯一匹配的重量立即进入订单“已发胶料”；无法唯一判断的明细完整保留为待关联记录，绝不会猜测分摊。
- 下单日期优先结合文件名和表格右上角“发单时间”识别；两者冲突时预检会明确警告并采用文件名日期。发料日期来自文件名、右上角“打印日期”或标准模板的“发料日期”，与每批胶料的制造日期分开保存和展示。
- 订单管理中的“导入记录”长期保存每次预检、失败和提交结果；无法识别的文件及逐行跳过内容均记录文件名、时间、工作表、行号、业务标识和具体原因，可随时回看或下载问题报告。
- 自有总表中非空的“已发胶料”按手工补充量保留，并与发料明细累计相加；预检会提示核对，避免同一批胶料同时出现在手工值和发料清单中而重复统计。
- 每条订单记录来源单据时间、最近导入时间和总体最后更新时间；总体时间同时考虑订单资料、发料、生产和出货的最近变化，完整变更仍保留在不可修改的审计历史中。
- 生产计划和生产记录可直接选择同一订单明细与产品规格；品检出货继续引用同一订单主档，绩效分析优先按订单明细ID汇总，历史文本记录才回退到订单号匹配。
- 导入原文件存放在媒体目录中，会随SQLite和图片一起进入现有备份；Nginx明确阻断`/media/business-imports/`的公网访问，业务API也不返回原文件地址。公开Git仓库和Docker构建上下文均排除真实业务Excel。

## 前端生产管理

- 默认初始化三组双联机台，共6台（第一组1/2、第二组3/4、第三组5/6）；机台组、组内位置和机台编号由数据库动态管理，可继续新增分组及机台，重复初始化不会删除或停用扩展站位。
- 实时看板显示已上机模具型号、订单、上模、最近换料、预计换模和倒计时；待上机计划在下方独立看板展示，确认上机后会同步移出货架并进入实时看板。
- 空闲机台可直接选择在库模具“快速上机 / 试模”，完全跳过生产计划和订单；也可选择先上机再登记正式生产，或继续使用待上机计划。
- 已有待上机计划时，快速试模只允许选择该计划模具；试模结束可直接选择库位归位，原待上机计划继续保留，也可直接打开计划确认正式生产。
- “停机 / 结束生产”只结束当前生产记录，模具仍留在机台；“结束生产并下机归位”会在一个数据库事务内同时完成生产、选择库位、释放机台和更新模具状态，任何库位校验或叠放确认失败都会整笔回滚。
- 未关联生产单的已上机模具可直接登记生产、下机归位或标记客户收回；归位后货架、模具台账、实时看板和移动历史同步更新。
- 每张生产订单可按天、按作业员补录生产模数，完工后统一登记良品、不良、材料、人工、能耗及其他成本。
- 系统自动计算计划模数、预计换模时间、实际工时、完成进度、欠模数、收入、成本、利润和工时效率，并保留结算修订记录。
- “生产订单统计表”采用“每个工作表一张订单卡”的格式；填写系统中已启用的机台编号，默认编号为 `1`–`6`，也支持 `D01` 等扩展编号，并兼容旧台账 `A01`、`A02`、`B01`、`B02`、`C01`、`C02`；上传后先预检，整批无错误才会事务化写入数据库。
- 生产Excel历史导入只建立生产记录，不会改变模具台账中的当前库位或状态。
- 新增适配纸质机台账的“生产手工账”：先按订单号＋项次建立任务，只需确认订单与有效孔数，机台、具体模具、人员和日期均可留空后补；选择具体实物模具后可把本次孔数保存为该模具默认孔数。
- 交接时只填写机台当前累计模数，系统按同一计数分段的“本次读数－上次读数”计算本人的模数。例如100→200→280会分别记为100、100、80模；机台计数清零会新建分段而不删除历史。修改或取消中间记录后，后续人员产量会按顺序自动重算。
- 白班、夜班自动建议但可修改；人员、班次、日期和协助人员均可后补，日期清空后仍计入订单总进度，但不进入按日趋势。一个订单可有多个生产段或多副模具并行，订单页汇总所有正常记录的理论件数、欠产量和超产量。
- 手工账任务达到目标模数后可确认结束；未达目标也允许填写原因后提前结束并另开生产段。记录不良数量为选填，订单生产进度按 `每条实际模数×该条孔数－已登记不良` 汇总。

## 品检出货与退货返工

- 新增“流程卡出货”工作流：流程卡数量来自流程卡记录，成品单重按产品规格/模具型号维护并在流程卡上冻结快照；胶料发料重量只做对照，不会被误当成成品单重。
- 出货支持一张流程卡分批、多张流程卡合并成同一批次，实际净重按 `流程卡数量×成品单重÷1000` 计算；超过理论重量110%会阻断，低于理论重量只提醒。出货日期缺失的草稿必须补齐后才能确认，补录历史日期必须填写原因。
- 每个实际包装批次按一张流程卡独立追踪；同一订单一次出货10批后，其中两批退回时，两批各自都是“第1次退货返工”，不会误记成整个订单返工2次。同一张卡对应的物理批次再次退回时才自动接续为第2次、第3次。
- 出货时扫描流程卡为选填，仍可使用相同称重批数快速登记；退货时优先用手机连续扫描随货返回的流程卡，系统自动找到原出货批次、带出订单/项次/规格/材质并建立独立返工追踪。首次处理旧数据找不到绑定时，只需补选一次原出货物理批次。
- 已确认出货可通过详情中的“纠正出货”修改出货单号、流程卡标准数量、成品单重、实称重量、相同称重批数及扫码卡号；卡号支持直接改写、扫码替换、单个解绑或全部清空。保存时系统在一个数据库事务内重新计算件数、跨订单分配、订单状态和流程卡状态，并保留修改前后快照及纠正原因；任一校验失败会完整回滚原记录。
- 无退货/返工关联的已确认出货可填写原因后整单作废，订单可出货余量和流程卡状态会同步恢复，原出货明细及分配仍作为审计历史保留。已经产生退货、返工或退货分配的出货禁止直接纠正或作废，避免破坏物理批次追踪链。
- 客户退回会从订单净有效出货量中扣除并自动重新打开已完成订单；返工完成后可从原记录直接重新出货，净出货达标时订单再次自动完成。内部返工继续与客户退回分开统计，不计入前端生产进度。
- 流程卡丢失可执行“补卡换号”：新卡继承同一物理批次的完整出货、退货和返工轮次，旧卡保留审计并标记作废，扫码旧卡时会提示当前有效卡号。
- 流程卡、成品单重、批量出货、返工主案和返工轮次均保留不可删除的操作记录；旧的件数制出货和返工接口/历史数据继续可用。
- 员工档案使用唯一工号，分别标记品检、返工或兼任岗位，停用员工不会丢失历史绩效记录。
- 每批出货记录出货单号、日期、订单批次、责任品检员、质检数量、合格数量、不良数量和实际出货数量。
- 每次退货返工关联原出货单，分别记录责任品检员和实际返工处理员工，避免把质量责任与返工工作量混在一起。
- 系统自动校验“质检数＝合格数＋不良数”“出货数不超过合格数”“返工合格＋报废不超过返工数”，并限制累计退货数量不得超过原出货数量。
- 页面按日期区间展示每日趋势、订单批次统计和员工绩效依据，包括质检量、一次合格率、责任退货量、返工处理量和返工通过率。
- 同一出货单关联的返工事件超过3次时显示红色预警；业务记录保留审计，不开放直接删除。

## 数据分析与绩效

- 独立页面按月份汇总生产、品检、出货、退回返工和收支，人员、机台和分组全部从数据库动态读取，不限制人数或机台数量。
- 系统自动数据、手工补录数据和合计结果分开标识；没有生产计划或历史资料不完整时，也可直接通过页面补录绩效和收支，不依赖Excel。
- 自动利润只统计已结算生产单并按结算时间归属；手工收入、材料、人工、能耗和其他支出按实际发生日期归属，同时提示未结算完工单。
- 展示收入、成本、利润、利润率、折算工时、实际机时、填报工时、机台效率、人员产量、一次合格率、退回率、返工通过率、原因排行和订单联动趋势。
- 新记录优先通过同一订单明细ID关联生产与品检出货；历史记录没有订单外键时才按规范化后的相同订单号回退汇总，页面会标明关联方式。比例分母为0时显示空值，不用0%掩盖缺少数据。
- 手工记录可编辑、作废和恢复；作废记录保留在历史中但不再进入分析，不进行物理删除。
- 设计目标是轻量小厂易用，同时保留动态扩展能力；初始规模和当前人员数量都不是程序上限。

## 检查与测试

不调用Docker的部署文件静态检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-deployment.ps1
```

项目测试：

```powershell
cd backend
D:\develop\venvs\erp\Scripts\python.exe manage.py test molds orders production quality analytics --verbosity 1

cd ..\frontend
$env:PATH='D:\develop\node22;'+$env:PATH
D:\develop\node22\npm.cmd run lint
D:\develop\node22\npm.cmd run test
D:\develop\node22\npm.cmd run build
```

## 重要限制

### 自动同步网站、Android 与微信小程序

- 每次推送到 `main` 后，容器工作流会先运行后端/前端/Compose 检查，再构建并推送 GHCR 镜像。
- `.github/workflows/deploy-server.yml` 会在镜像工作流成功后，使用 SSH 连接生产服务器；连接成功时先执行在线备份，再同步最新 `compose.yaml`、拉取镜像、启动服务并等待健康检查。
- 服务器部署工作流是可选的。需要在 GitHub 仓库 `Settings → Secrets and variables → Actions` 设置：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_PATH`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`；可选 `DEPLOY_PORT`（默认22）。未设置时工作流会明确跳过，不会误报已部署。
- Android 应用和微信小程序的网页入口都固定加载 `https://erp.qvgro.com`。网站/API镜像更新并在服务器重启后，重新打开或刷新即可使用新版本，不需要重新安装 APK 或重新上传小程序网页页面。
- Android 原生代码、权限、图标或小程序原生首页变化不能通过网页部署替代。Android 需要同一签名的新版 APK；小程序原生代码可由 `.github/workflows/miniprogram-upload.yml` 上传开发版，但仍需配置 `WECHAT_MINIPROGRAM_APPID` 和 `WECHAT_MINIPROGRAM_PRIVATE_KEY_BASE64`，并由微信后台人工审核、发布。
- 小程序上传密钥只存在 GitHub Runner 临时目录，不写入仓库、构建产物或日志。微信平台的 IP 白名单、业务域名、备案和审核要求仍由管理员维护。

- 系统目前仍使用共用登录账号，可记录责任员工和返工员工，但不能区分具体的系统录入经办人。
- 状态由人员手动更新，不连接设备自动判断。
- 当前支持流程卡二维码扫码，但不包含离线写入、设备自动采集和智能排程。网页扫码必须使用HTTPS并授予浏览器相机权限；Android App已申请相机权限，识别不可用时仍可手动输入完整流程卡号。
- Python 3.11预计在2027年10月结束安全维护，应在此前升级到仍受支持的Python版本。
