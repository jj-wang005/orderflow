# OrderFlow

**OrderFlow** · GitHub 仓库建议名：`orderflow`

从零搭建的前后端完整项目，基于 FastAPI、Vue 3 和 MySQL 实现采购订单处理工作台。将不同客户的采购文件转换为统一字段，匹配合格历史订单，创建销售订单，并保留采购行、导入任务和订单变更之间的追溯关系。

## 开发过程

项目从零搭建，经历基础工程、数据库与配置、Excel 解析、历史匹配、自动建单、前端联调，以及客户管理、批量导入、AI 草稿和订单导出等迭代。开发过程使用了 AI 辅助编码与整理，不宣称全程纯手工完成。

## 已实现功能

- 客户配置管理：字段映射、匹配规则、配置预览和版本记录。
- Excel 单文件及批量导入：字段标准化、逐行校验、历史匹配和异常处理。
- 导入防重：请求幂等、重复采购提示、人工核实及失败任务重传。
- 采购行人工补录、手动建单、订单编辑、状态流转和作废审计。
- 销售订单查询、筛选、Excel 导出和详情 PDF 导出。
- PDF、图片及文本的 AI 解析草稿，人工确认后再导入。
- C豆智能助手及操作指南；演示与测试环境隔离。

项目用于受控本地演示，尚无用户鉴权，未对接真实 OMS/ERP。样例历史订单及产品价格为模拟数据；AI 默认 Mock，接入 MiMo 需要后端配置和界面授权。

## 业务流程与设计

```mermaid
flowchart LR
    A[采购文件或文本] --> B[Excel 标准化或 AI 解析草稿]
    B --> C[字段校验与历史匹配]
    C --> D[自动建单或人工处理]
    D --> E[任务追踪与订单管理]
    E --> F[Excel 与 PDF 导出]
```

- **规则可配置**：按客户配置字段映射与历史匹配规则，任务保留配置版本快照。
- **不确定时交给人工**：匹配失败、产品歧义和疑似重复采购提供明确处理结果；AI 草稿需要确认。
- **金额由后端计算**：复用合格历史参考中的产品和单价，按本次采购数量计算订单金额。
- **操作可追溯**：保留采购来源、逐行结果及订单变更记录；幂等请求用于安全重试。

## 技术与目录

Python 3.11、FastAPI、SQLAlchemy、Alembic、MySQL；Vue 3、TypeScript、Vite、Element Plus；Pytest、Vitest。

```text
app/                 后端 API、业务服务、模型及助手知识
frontend/            前端工作台及组件测试
alembic/             数据库迁移
config/              客户规则、模拟历史数据和环境配置示例
sample_files/        Excel、PDF 和图片样例
tests/               后端测试及隔离数据库 fixture
scripts/             环境管理、初始化及验证脚本
docs/                项目架构与环境使用说明
```

## 首次安装

克隆仓库或解压下载包后，进入 `OrderFlow` 根目录。以下命令在 Windows PowerShell 中执行。需要可用的 MySQL 8.4 和 Node.js 22.12 或更高版本。选择已安装项目依赖的 Python，或创建独立环境：

```powershell
conda create -n order_workbench python=3.11
conda activate order_workbench
python -m pip install -r requirements-lock.txt
Push-Location frontend
npm ci
Pop-Location
```

仅在新环境中复制配置模板：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，填写 MySQL 地址及管理员凭据，保留 `APP_ENV=history`、`DB_NAME=order_automation`。初始化需要管理员具备建库、建账户、授权和迁移权限；日常应用使用初始化脚本生成的独立运行账户。

仅当 `order_demo`、`order_test`、对应运行账户和本地环境配置均不存在时执行一次：

```powershell
python -m scripts.environment_admin initialize
```

脚本创建两套环境、迁移至当前版本、写入模拟样例，并生成 `.env.demo`、`.env.test` 及 `.runtime/` 身份文件。已有目标会拒绝覆盖；不要在现有环境重复初始化。详情见[环境使用说明](docs/environments/README.md)。

## 启动、停止与重启

演示环境配置完成后，在项目根目录激活已安装依赖的 Python 环境，使用两个 PowerShell 终端分别启动。

终端一，启动后端：

```powershell
$env:APP_ENV = 'demo'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```

终端二，启动前端：

```powershell
cd frontend
$env:API_PROXY_TARGET = 'http://127.0.0.1:8011'
$env:CVTE_BACKEND_PORT = '8011'
npm run dev -- --mode demo --port 5181 --strictPort
```

打开 http://127.0.0.1:5181，后端文档为 http://127.0.0.1:8011/docs，健康检查为 http://127.0.0.1:8011/health。页面右上角应显示演示环境及 `order_demo`。

两个终端分别按 Ctrl+C 停止；重启时重新运行对应命令。停止与重启保留数据库数据。换用其他端口时，前端代理端口需与后端保持一致。

本机附带的根目录 `start_*.ps1`、`stop_*.ps1`、`reset_*.ps1`、`restore_*.ps1` 仅供本地使用，已加入 `.gitignore`，不属于仓库必需文件。重新下载项目后可以按上面的命令运行，无须这些脚本。详细环境与维护机制见[环境使用说明](docs/environments/README.md)。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
Push-Location frontend
npm test
npm run build
Pop-Location
```

MySQL 集成测试需显式设置 `RUN_DB_TESTS=1`，并使用可创建临时 schema 的测试配置；测试连接由 fixture 限制在临时数据库。未启用时的跳过项不代表数据库验证通过，具体约束见[环境使用说明](docs/environments/README.md)。

## 文档与提交范围

- [项目架构与文档导航](docs/README.md)：处理流程、模块职责和实现边界。
- [演示与测试环境](docs/environments/README.md)：安装、启动、重置和恢复。
- [前端说明](frontend/README.md)：路由入口和前端开发。

此上传版保留运行源码、测试和必要说明；原开发过程报告、数据库快照、聊天归档和截图保留在本地原项目中。

`.gitignore` 排除真实环境配置、虚拟环境、依赖目录、构建产物、缓存、日志、备份、IDE 配置、聊天归档及答辩输出。示例配置保留，数据库内容不会随 Git 上传。部分内部缓存键和运行账户沿用兼容命名，以保护已有操作记录和本地环境。
