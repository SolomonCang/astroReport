# astroReport

私人文献推送系统，托管在 GitHub Actions。

## 功能

- 每天北京时间 08:30 自动抓取 arXiv 指定子分类
- 使用 OpenAI 兼容接口自动生成中文完整报告与精简摘要（支持自定义 endpoint）
- 完整报告写入仓库，云端保留 10 天
- 每天通过 Resend 邮件发送精简版，含完整版链接与论文链接

## 目录

- .github/workflows/daily-report.yml: 每日主流程
- .github/workflows/manual-test-report.yml: 手动邮件测试流程
- .github/workflows/cleanup-report.yml: 过期清理流程
- config/arxiv.json: 抓取配置
- skill.md: LLM 摘要重点指令
- scripts/run_daily.py: 主入口
- scripts/cleanup_reports.py: 清理入口
- reports/index.json: 报告索引

## 1. 配置 GitHub Secrets

在仓库 Settings > Secrets and variables > Actions 中添加：

- OPENAI_API_KEY
- OPENAI_MODEL: 可选，默认 gpt-4o-mini
- OPENAI_API_BASE: 可选，类 OpenAI 服务 endpoint/base URL
- RESEND_API_KEY
- RESEND_FROM_EMAIL: 例如 noreply@你的域名
- REPORT_RECIPIENT_EMAIL: 你的邮箱

OPENAI_API_BASE 示例：

- `https://api.openai.com/v1`
- `https://your-provider.example.com/v1`
- `https://your-provider.example.com/v1/chat/completions`

## 2. 修改抓取范围

编辑 config/arxiv.json：

- categories: arXiv 子分类数组
- max_results: 最大抓取条数
- lookback_hours: 回看时间窗口
- max_papers_in_report: 报告最多收录篇数

## 3. 手动测试

进入 Actions 页面，手动运行 Manual Email Test。

该流程会直接发送一封测试邮件，用于验证 Resend 密钥、发件邮箱和收件邮箱配置是否可用。


运行成功后会在日志中看到 `manual email test sent`。

运行成功后检查：

- 收件邮箱是否收到测试邮件

## 4. 摘要重点（skill.md）

总结脚本会自动读取仓库根目录的 `skill.md`（若不存在则尝试 `config/skill.md`），并把内容注入到 LLM 提示词中作为重点方向。

当前默认重点包含：

- 恒星磁场
- 磁活动
- 与以上两者相关的系外行星研究

## 5. 10天自动删除

Cleanup Expired Reports 工作流每天运行一次，删除超期报告。

## 说明

- 当前是单收件人模式，收件邮箱通过 REPORT_RECIPIENT_EMAIL 控制
- 邮件发送失败不会阻塞报告产出