# 演示与手动测试环境

> 本文中的根目录 PowerShell 包装脚本为本机辅助文件，已忽略提交。仓库使用者请先按[项目 README](../../README.md)中的双终端命令启动；下述脚本说明用于已保留本机脚本的环境。

订单支持作废、Excel 导出和详情 PDF 导出，并提供页面操作指南。更新代码只需使用对应 stop/start 脚本重启应用。日常导入与人工操作产生的新记录保留，不能为验收清空演示库。列表可作废已创建业务订单、导出Excel，详情可导出内部PDF，右下角“?”查看指南。

在项目目录打开 PowerShell。首次使用先完成下方初始化；已有环境直接使用启动脚本。启动不清理数据、不抢占端口。

| 环境 | 启动 | 页面 | 后端 | 数据库 |
| --- | --- | --- | --- | --- |
| 演示 | `.\start_demo.ps1` | http://127.0.0.1:5180 | 127.0.0.1:8010 | order_demo |
| 手动测试 | `.\start_test.ps1` | http://127.0.0.1:5174 | 127.0.0.1:8001 | order_test |

演示端口避开原有 8000 服务。页面右上角显示“演示环境 · order_demo”或“测试环境 · order_test”。也可打开对应网址的 `/health` 检查 environment、database_name。前端写请求会再次检查环境，代理目标不符时拒绝提交。

已运行时重复启动会拒绝。重启先执行 `.\stop_demo.ps1` 或 `.\stop_test.ps1`，再启动，数据保留。停止脚本核对 PID、可执行文件及命令行，只停止本项目记录的进程。日志在 `.runtime/demo/`、`.runtime/test/`。支持 `-BackendPort`、`-FrontendPort` 指定未占用端口；`-PythonPath` 指定安装了项目依赖的 Python；启动脚本兼容原本机环境，其他电脑建议显式指定当前解释器。前端需已有 node_modules。

## 安全恢复演示初始数据

1. `.\stop_demo.ps1`，关闭连接 order_demo 的数据库工具及其他应用。
2. `.\reset_demo.ps1 -Plan`，检查目标和清理表范围。
3. `.\reset_demo.ps1`，人工输入完整确认内容 `RESET order_demo`。
4. 成功后 `.\start_demo.ps1`。

清理该库的任务、采购行、销售订单、状态及修正记录、AI 草稿、配置版本和客户配置，再重建 A/B/C 场景。保留 schema、迁移记录及环境身份。先生成 `.backups/demo/` JSON 备份及 SHA256，读回验证成功才清理。业务删除、重建及匹配验证处于同一事务；失败回滚并保留备份。不重置自增序号，ID 间隙正常。

恢复已有备份：停止应用后执行 `.\restore_demo.ps1 -BackupPath '备份JSON的完整路径'`，检查目标后输入 `RESTORE order_demo`。恢复前也备份当前数据。跨环境备份、错误校验和、未知表结构均拒绝。失败后保留终端提示及备份文件，不要手工删表。

测试环境独立使用 `.\stop_test.ps1`、`.\reset_test.ps1 -Plan`、`.\reset_test.ps1`，确认 `RESET order_test`；恢复 `.\restore_test.ps1 -BackupPath '测试备份完整路径'`，确认 `RESTORE order_test`。脚本没有任意库名参数。原库不在重置白名单。

## 配置与首次安装

独立读取根目录 `.env.demo`、`.env.test`；文件缺失、连接失败、环境与库名不符均停止，不回退根 `.env`。运行账户仅有各自数据库 SELECT/INSERT/UPDATE/DELETE。维护脚本显式使用根 `.env` 管理员做初始化和备份维护；应用没有管理员回退连接。

实际配置、密钥、`.runtime/`、备份均忽略提交。妥善保存 `.env.demo`、`.env.test` 及 `.runtime/demo.identity.json`、`.runtime/test.identity.json`；身份文件丢失或不符时维护拒绝，不能随意伪造。备份不含密码和 API Key。config/environments/*.env.example 仅为无密钥模板。

仅在全新 MySQL、两个目标库及账户均不存在时，根 `.env` 配置有建库建账户权限的管理员，在根目录执行：

```powershell
python -m scripts.environment_admin initialize
```

已有目标一律拒绝，不接管、不覆盖。部分初始化失败也停止，保留已建内容排查，重复执行不会覆盖。

原 order_automation 保持原数据和结构，未升级或初始化；默认 history 应用仅允许读。普通无 mode 的 `npm run dev` 仍指向 8000 历史服务；演示、测试使用一键脚本。

## 样例与测试

原 sample_files 三份 Excel 未修改。22 条采购行历史参考匹配 A 8/9、B 4/5、C 7/8；模拟产品 SIM-DEMO 前缀。全新、无已知重复风险的隔离场景已验证 A 8/9、C 7/8 自动建单。A 同单号不同料号不再相互拦截；C 不要求原 Excel 中不存在的采购单号。历史匹配与采购身份不同：同一任务行不重复建单；跨任务相同文件或疑似相同采购均需人工核实，再次采购可填写真实说明继续处理，稳定采购明细编号可选，不能仅按料号、批次和数量认定重复或新采购。现有任务不会因升级自动重跑。

order_test 另有 TEST-* 边界订单。pytest 使用回滚和独立临时 schema：

```powershell
$env:APP_ENV='test'
$env:RUN_DB_TESTS='1'
python -m pytest tests/test_environment_isolation.py tests/test_ai_drafts.py tests/test_ai_concurrency.py tests/test_v2_batch_import.py tests/test_v2_batch_concurrency.py -q
Remove-Item Env:RUN_DB_TESTS
Remove-Item Env:APP_ENV
```

并发 fixture 建临时库需要维护管理员权限；失败不会改连业务库。手动测试操作可持久保存，重置需独立确认。演示 AI 沿用本机后端 MiMo 配置，仍须逐次明确外发授权；测试默认 Mock。本任务未调用外部 MiMo。

环境实现入口为 `scripts/environment_admin.py`、`scripts/environment.ps1` 和 `app/core/config.py`。
