# astroReport

私人文献推送系统，托管在 GitHub Actions。

## 功能

- 每个工作日北京时间 10:00 自动抓取 arXiv 指定子分类（周末不触发）
- 使用 OpenAI 兼容接口自动生成中文完整报告与精简摘要（支持自定义 endpoint）
- 完整报告写入仓库，云端保留 15 天（约10个工作日）
- 每天通过 Resend 邮件发送精简版，含完整版链接与论文链接

## 目录

- .github/workflows/daily-report.yml: 每日主流程
- .github/workflows/manual-test-report.yml: 手动邮件测试流程
- .github/workflows/cleanup-report.yml: 过期清理流程
- config/config.json: 全局系统配置（arXiv 抓取 + 日报行为）
- config/focus_area.md: LLM 摘要重点指令
- scripts/run_daily.py: 主入口
- scripts/cleanup_reports.py: 清理入口
- reports/index.json: 报告索引

## 1. 配置 GitHub Secrets

在仓库 Settings > Secrets and variables > Actions 中添加：

- OPENAI_API_KEY
- OPENAI_MODEL: 可选，默认 gpt-4o-mini
- OPENAI_API_BASE: 可选，类 OpenAI 服务 endpoint/base URL
- RESEND_API_KEY

发件邮箱通过 `config/config.json` 的 `email.from_email` 配置，当前为 `report@astrocang.com`；收件邮箱通过 `mail_list` 配置。日报和手动邮件测试共用这份配置。

OPENAI_API_BASE 示例：

- `https://api.openai.com/v1`
- `https://your-provider.example.com/v1`
- `https://your-provider.example.com/v1/chat/completions`

## 2. 修改配置

编辑 `config/config.json`，包含 arXiv 抓取与日报系统的全部设置：

```json
{
  "arxiv": {
    "categories": ["astro-ph.EP", "astro-ph.SR"],
    "max_results": 200,
    "lookback_hours": 48
  },
  "report": {
    "dedup_lookback_days": 15,
    "retention_days": 15
  }
}
```

字段说明：

- `arxiv.categories`: arXiv 子分类数组
- `arxiv.max_results`: 每页抓取条数（脚本会分页拉取直到覆盖回看窗口）
- `arxiv.lookback_hours`: 抓取时间回看窗口（小时）
- `report.dedup_lookback_days`: 与历史日报去重的回看天数（默认15）
- `report.retention_days`: 报告保留天数，超期后由清理工作流删除（默认15）

## 3. 手动测试

进入 Actions 页面，手动运行 Manual Email Test。

该流程会直接发送一封测试邮件，用于验证 Resend 密钥、发件邮箱和收件邮箱配置是否可用。
可填写 `recipient_email`，只向指定邮箱发送测试；留空时发送到配置文件中的整个 `mail_list`。


运行成功后会在日志中看到 `manual email test sent`。

运行成功后检查：

- 收件邮箱是否收到测试邮件

若 Resend 域名显示 Failed，检查阿里云 DNS 中下列 `astrocang.com` 记录是否启用，并与 Resend Domains 页面保持一致：

- `resend._domainkey` 的 TXT 记录：使用 Resend 提供的完整 DKIM 公钥。
- `rsend` 的 CNAME 记录：`rsend-apne1.forge.rmta.net`。
- `send` 的 CNAME 记录：`send.forge.rmta.net`。

以上是新域名在东京区域生成的配置；新增其他域名时应使用 Resend 为该域名提供的记录。修正或重新启用后，在 Resend 域名详情点击 Restart，等待验证状态变为 Verified。邮件失败不会阻止日报文件生成，因此 Actions 显示成功时仍需检查日志中的 `email_sent`；Resend 返回的错误会显示为工作流警告。

## 4. 摘要重点（config/focus_area.md）

总结脚本会优先读取 `config/focus_area.md`（兼容旧路径 `config/skill.md` 和 `skill.md`），并把内容注入到 LLM 提示词中作为重点方向。

全局总结会结合这些重点方向，并在开头标注相关文献编号，格式示例：`[相关文献编号: 2, 5, 9]`，用于快速定位下方条目。

当前默认重点包含：

- 恒星磁场
- 磁活动
- 与以上两者相关的系外行星研究

## 5. 15天自动删除

Cleanup Expired Reports 工作流每天北京时间 10:20 运行一次，删除超过15天的报告（约10个工作日）。

清理脚本优先使用 `expire_at` 判定，若时间字段异常则会回退到 `created_at + 15天`（再回退到 `date + 15天`）进行删除判断。

## 说明

- 收件邮箱通过 `config/config.json` 的 `mail_list` 控制
- 邮件发送失败不会阻塞报告产出
