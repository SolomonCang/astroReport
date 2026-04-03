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

## 0. 前置准备

### 0.1 申请 Resend API Key

Resend 是本项目用于发送每日邮件的服务。

1. 访问 [https://resend.com](https://resend.com) 注册账号（免费计划每月可发 3000 封）
2. 进入 **Domains** 页面，点击 **Add Domain**，添加并验证你拥有的域名（按提示在 DNS 服务商处添加 MX、TXT、DKIM 记录）
3. 域名验证通过后，进入 **API Keys** 页面，点击 **Create API Key**，生成一个密钥
4. 将该密钥填入仓库 Secret `RESEND_API_KEY`
5. `RESEND_FROM_EMAIL` 填写使用已验证域名的发件地址，例如 `noreply@你的域名`

> **注意**：Resend 要求发件地址的域名必须经过验证，否则发送会失败。

### 0.2 接入 LLM 接口（OpenAI 或兼容服务）

本项目通过 OpenAI Chat Completions 接口生成中文摘要，支持 OpenAI 官方及所有兼容服务。

#### 使用 OpenAI 官方接口

1. 访问 [https://platform.openai.com](https://platform.openai.com)，注册并登录
2. 进入 **API Keys** 页面，创建一个 API Key
3. 将该密钥填入仓库 Secret `OPENAI_API_KEY`
4. `OPENAI_MODEL` 可留空（默认 `gpt-4o-mini`）或填写其他模型名，如 `gpt-4o`
5. `OPENAI_API_BASE` 可留空

#### 使用类 OpenAI 兼容服务（国内可用）

任何兼容 OpenAI Chat Completions 格式的服务均可接入，例如：

| 服务商 | 官网 | OPENAI_API_BASE 示例 |
|---|---|---|
| 硅基流动 (SiliconFlow) | https://siliconflow.cn | `https://api.siliconflow.cn/v1` |
| DeepSeek | https://platform.deepseek.com | `https://api.deepseek.com/v1` |
| 阿里云百炼 | https://bailian.console.aliyun.com | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 字节豆包 (Ark) | https://www.volcengine.com/product/ark | `https://ark.cn-beijing.volces.com/api/v3` |

接入步骤（以任意兼容服务为例）：

1. 在对应服务商官网注册并开通 API 访问权限
2. 在控制台创建 API Key
3. 将 API Key 填入 `OPENAI_API_KEY`
4. 将服务商提供的 base URL 填入 `OPENAI_API_BASE`
5. 将对应模型名称填入 `OPENAI_MODEL`

> **提示**：`OPENAI_API_BASE` 既可以是不带路径后缀的 base URL（如 `https://api.siliconflow.cn/v1`），也可以是完整的 chat completions 端点（如 `https://api.siliconflow.cn/v1/chat/completions`），两者均可识别。

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

## 2. 修改配置

编辑 `config/config.json`，包含 arXiv 抓取与日报系统的全部设置：

```json
{
  "arxiv": {
    "categories": ["astro-ph.EP", "astro-ph.SR"],
    "max_results": 80,
    "lookback_hours": 36
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


运行成功后会在日志中看到 `manual email test sent`。

运行成功后检查：

- 收件邮箱是否收到测试邮件

## 4. 摘要重点与提示词修改（config/focus_area.md）

### 4.1 文件作用

总结脚本会优先读取 `config/focus_area.md`（兼容旧路径 `config/skill.md` 和 `skill.md`），并把文件内容注入到 LLM 提示词的 `focus_skill` 字段中。

LLM 收到的指令会要求：

- 每篇个人摘要优先提取 `focus_skill` 中指定的重点信息
- 全局总结必须体现 `focus_skill` 关注方向，并给出最相关文献编号（`related_ids`）

因此，**修改 `config/focus_area.md` 即可直接改变 LLM 的总结方向**，无需修改任何脚本代码。

### 4.2 如何修改提示词以调整总结方向

编辑 `config/focus_area.md`，按照以下结构描述你关注的方向：

```markdown
# 摘要重点说明

你在撰写每篇文献摘要与全局总结时，请优先关注以下方向，并在摘要中明确写出相关信息。

## 1) 关注方向A

- 具体关注点1（例如：研究对象的类型与演化阶段）
- 具体关注点2（例如：使用的观测或分析方法）
- 具体关注点3（例如：核心结论或物理量）

## 2) 关注方向B

- ...

## N) 输出偏好（可选）

- 每篇摘要优先回答："研究用了什么方法、得到什么核心结论、与方向A/B的关系是什么"
- 若论文与上述方向弱相关，也请简短指出其可能关联点；若完全无关，可保持常规摘要
- 关键词尽量具体，优先给出物理过程、观测手段、目标天体类型
```

**修改建议**：

- **增加方向**：在文件中新增 `## N) 方向名称` 小节，并列出具体关注点，LLM 会将其纳入总结范围
- **删除方向**：删除对应小节，LLM 将不再优先关注该主题
- **细化方向**：在小节下添加更具体的子条目（如具体方法名、天体类型、物理量），可让摘要更精准
- **调整输出风格**：在"输出偏好"小节中说明你希望摘要的侧重点或格式要求

### 4.3 生效范围

`config/focus_area.md` 的内容同时影响两类输出：

| 输出字段 | 影响方式 |
|---|---|
| 每篇文献摘要（`items[].summary`） | 优先提取与重点方向相关的方法、结论、关键词 |
| 全局总结（`global_summary`） | 必须体现重点方向，并标注最相关文献编号（`related_ids`），格式示例：`[相关文献编号: 2, 5, 9]` |

### 4.4 当前默认重点

当前 `config/focus_area.md` 中包含以下方向（可按需修改）：

- 恒星磁场
- 磁活动
- 与以上两者相关的系外行星研究
- DIB（弥漫星际带）
- 尘埃与消光
- 复杂分子（分子天文）

## 5. 15天自动删除

Cleanup Expired Reports 工作流每天北京时间 10:20 运行一次，删除超过15天的报告（约10个工作日）。

清理脚本优先使用 `expire_at` 判定，若时间字段异常则会回退到 `created_at + 15天`（再回退到 `date + 15天`）进行删除判断。

## 说明

- 当前是单收件人模式，收件邮箱通过 REPORT_RECIPIENT_EMAIL 控制
- 邮件发送失败不会阻塞报告产出