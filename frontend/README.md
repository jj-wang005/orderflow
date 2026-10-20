# OrderFlow · 前端

Vue 3、TypeScript、Vite 和 Element Plus 构建的本地销售运营工作台。数据及业务校验由 FastAPI 提供；C豆和操作指南保留。

## 日常运行

推荐从项目根目录按[项目 README](../README.md)完成首次安装，再按其中的双终端命令分别启动后端与前端。本机 PowerShell 包装脚本已忽略提交，下载仓库后无需依赖这些脚本。

| 环境 | 页面 | 代理后端 |
| --- | --- | --- |
| 演示 | http://127.0.0.1:5180 | http://127.0.0.1:8010 |
| 测试 | http://127.0.0.1:5174 | http://127.0.0.1:8001 |

## 前端开发

Node.js 要求 22.12 或更高版本。在当前目录执行：

```powershell
npm ci
npm run dev
```

默认开发地址为 http://127.0.0.1:5173，代理到本机 8000 历史后端；该环境仅允许读取。需要完整录单操作时按[项目 README](../README.md)中的演示环境双终端命令启动，详情见[环境说明](../docs/environments/README.md)。

若 PowerShell 限制 `npm.ps1`，使用 `npm.cmd` 执行相同命令。接口目标可参考 `.env.example` 配置，前端环境变量不得填写数据库密码或 API 密钥。

## 页面入口

| 地址 | 功能 |
| --- | --- |
| `/orders` | 销售订单列表、筛选及导出 |
| `/orders/:id` | 订单详情、状态及审计记录 |
| `/order-entry` | Excel 批量录入和 AI 解析草稿 |
| `/tasks` | 历史导入任务 |
| `/tasks/:id` | 任务采购行及异常处理 |
| `/customers` | 客户配置管理 |

首页进入销售订单列表。原 `/import`、`/ai/:id?` 和采购行书签保留兼容跳转。C豆提供知识问答及操作指南，不能代替人工确认订单。

## 检查与构建

```powershell
npm test
npm run typecheck
npm run build
```

`build` 已包含类型检查；产物位于 `dist/`，由 Git 忽略。浏览器中的缓存键沿用旧命名，以保留未完成操作的请求标识、草稿和 C豆位置。
