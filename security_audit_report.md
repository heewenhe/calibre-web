# Calibre-Web 新增功能安全审计报告

## 执行摘要

本次审计针对分支 `trae/solo-agent-9tHg1U` 合并到 `origin/master` 所引入的新增功能设计文档（`FEATURE_DESIGN.md` 和 `WIKI.md`）进行安全审查。新增功能包括元数据扫描、标签库管理和基于标签的文件组织。审计发现 **4 项中高风险问题**，主要集中在路径遍历、权限控制、跨站脚本（XSS）和数据一致性方面。建议在开发阶段修复这些问题，以避免生产环境中的安全隐患。

---

## 审计发现

### 🔴 高风险

#### AUDIT-001: 文件组织功能存在路径遍历风险

**位置**: `FEATURE_DESIGN.md` 第 520-528 行 (`FileOrganizerService.create_symlink`)

**问题描述**: `create_symlink` 函数直接使用用户配置的 `target_directory` 和图书标题构建软链接路径，未对输入进行充分校验：

```python
def create_symlink(self, book, target_directory):
    calibre_dir = os.path.join(config.config_calibre_dir, book.path)
    link_name = get_valid_filename(book.title, chars=96) + " (" + str(book.id) + ")"
    link_path = os.path.join(target_directory, link_name)
    # ...
    os.symlink(calibre_dir, link_path, target_is_directory=True)
```

`target_directory` 来自用户输入的规则配置，若未校验，攻击者可通过设置 `target_directory` 为 `/etc/cron.d/` 或 `../../敏感目录` 创建恶意软链接，可能导致：
- 任意文件/目录覆盖（若目标路径已存在）
- 系统目录污染（通过指向系统目录的软链接）

**影响**: 攻击者可能利用此漏洞覆盖系统文件或访问受限目录。

**修复建议**:
1. 对 `target_directory` 进行严格的路径校验，确保其在允许的基目录下（如 `config.config_calibre_dir` 的子目录）
2. 使用 `os.path.abspath()` 和 `os.path.commonpath()` 校验目标路径
3. 禁止包含 `..` 或绝对路径的外部目录
4. 示例修复代码：
   ```python
   import os
   
   def validate_target_directory(target_directory, base_dir):
       abs_target = os.path.abspath(target_directory)
       abs_base = os.path.abspath(base_dir)
       if not os.path.commonpath([abs_target, abs_base]) == abs_base:
           raise ValueError("Target directory must be within the allowed base directory")
       return abs_target
   ```

---

### 🟡 中高风险

#### AUDIT-002: 元数据扫描可能导致 SSRF（服务器端请求伪造）

**位置**: `FEATURE_DESIGN.md` 第 247-265 行 (`TaskMetadataScan`)

**问题描述**: 元数据扫描任务通过外部 provider（如豆瓣）获取图书信息。设计文档中未提及对 provider 请求的网络隔离或 URL 校验：

- 豆瓣 provider 通过 XPath 从 HTML 页面提取标签（`douban.py` 第 184 行）
- 扫描任务在后台线程中执行，可能长时间运行
- 若 provider 被劫持或配置为恶意 URL，可能导致服务器向任意地址发起请求

**影响**: 攻击者可能利用此漏洞扫描内网服务、访问云元数据端点（如 AWS 169.254.169.254）或进行 DoS 攻击。

**修复建议**:
1. 对 provider 的 URL 进行白名单校验，仅允许已知可信域名
2. 限制 HTTP 请求超时时间（如 10 秒）
3. 禁止访问私有 IP 地址段（10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16）
4. 在独立网络命名空间或代理中执行外部请求

---

#### AUDIT-003: 标签名称未转义可能导致 XSS 和注入攻击

**位置**: `FEATURE_DESIGN.md` 第 135-145 行 (`FileOrgRuleTags` 表设计)

**问题描述**: `FileOrgRuleTags.tag_name` 使用字符串直接关联 Calibre `Tags.name`，设计文档未提及输入校验和输出转义：

- 标签名称可能包含 HTML/JavaScript 代码（如 `<script>alert(1)</script>`）
- 在管理界面展示标签时，若未转义，可能导致存储型 XSS
- 标签名称可能包含 SQL 通配符或特殊字符，影响查询逻辑

**影响**: 攻击者可通过注入恶意标签名称，在管理员浏览标签库时执行任意 JavaScript 代码。

**修复建议**:
1. 在标签入库前进行输入校验，限制允许的字符集（如仅允许字母、数字、中文、常见标点）
2. 在前端模板中使用 Jinja2 的自动转义功能（确保 `| safe` 过滤器不被滥用）
3. 在 API 返回 JSON 时确保正确的 `Content-Type: application/json`
4. 对标签名称长度进行限制（如最多 100 字符）

---

### 🟡 中等风险

#### AUDIT-004: 软链接清理逻辑可能导致竞争条件

**位置**: `FEATURE_DESIGN.md` 第 520-528 行 (`create_symlink`) 和第 510 行 (清理逻辑)

**问题描述**: `create_symlink` 函数在创建新链接前先删除旧链接：

```python
if os.path.islink(link_path):
    os.unlink(link_path)
os.symlink(calibre_dir, link_path, target_is_directory=True)
```

这是一个非原子操作。在并发场景下（多个任务同时处理同一本书），可能出现：
- 检查 `islink` 后、删除前，链接被其他进程修改
- 删除后、创建前，其他进程访问到不存在的路径
- 符号链接指向被替换为恶意目录（TOCTOU 漏洞）

**影响**: 在并发环境下可能导致文件系统状态不一致或被恶意利用。

**修复建议**:
1. 使用文件锁（如 `fcntl.flock`）保护关键区域
2. 考虑使用原子操作（如先创建临时链接再重命名）
3. 在任务系统中确保同一本书不会被并发处理

---

### 🟢 低风险

#### AUDIT-005: 跨数据库关联缺乏外键约束可能导致数据不一致

**位置**: `FEATURE_DESIGN.md` 第 106-121 行 (`TagLibrary` 表) 和第 135-145 行 (`FileOrgRuleTags` 表)

**问题描述**: 设计文档明确说明不使用外键约束（因跨 SQLite 数据库），依赖应用层维护一致性：

- `TagLibrary.calibre_tag_id` 无 `ForeignKey` 约束
- `FileOrgRuleTags.tag_name` 使用字符串关联而非外键

虽然文档已提及此风险（风险4）并提供了缓解措施，但应用层一致性检查可能存在延迟或遗漏。

**影响**: 数据库层面无法保证引用完整性，可能导致孤儿记录或关联错误。

**修复建议**:
1. 在数据库操作层封装所有跨库关联逻辑，确保统一入口
2. 添加数据库触发器（如 SQLite 支持）或定期一致性检查任务
3. 在标签删除/重命名时，使用事务确保联动更新

---

#### AUDIT-006: 权限控制设计正确但需确保实施一致性

**位置**: `FEATURE_DESIGN.md` 第 590-597 行 (权限控制)

**问题描述**: 设计文档规定所有管理功能使用 `@admin_required` + `@user_login_required` 双重装饰器，这是正确的安全实践。但需要确保：

1. 所有新增 API 端点（`metadata_scheduler`、`tag_library`、`file_organizer`）都正确应用了装饰器
2. 前端路由也进行相应的权限校验（不仅依赖后端）
3. 任务启动 API（`/api/metadata_scan/start`、`/api/file_organizer/apply`）防止 CSRF

**影响**: 若装饰器遗漏或顺序错误，可能导致未授权访问。

**修复建议**:
1. 建立代码审查清单，确保每个新端点都有权限装饰器
2. 使用自动化测试验证未授权访问被正确拒绝
3. 对状态变更 API（POST/PUT/DELETE）启用 CSRF 保护（Flask-WTF 已集成）

---

## 总结

| 风险等级 | 数量 | 主要问题 |
|----------|------|----------|
| 🔴 高风险 | 1 | 路径遍历（AUDIT-001） |
| 🟡 中高风险 | 2 | SSRF（AUDIT-002）、XSS（AUDIT-003） |
| 🟡 中等风险 | 1 | 竞争条件（AUDIT-004） |
| 🟢 低风险 | 2 | 数据一致性（AUDIT-005）、权限实施（AUDIT-006） |

**优先修复建议**:
1. **立即修复**: AUDIT-001（路径遍历）- 在 `FileOrganizerService` 中添加路径校验
2. **高优先级**: AUDIT-002（SSRF）- 限制 provider 请求的目标地址
3. **高优先级**: AUDIT-003（XSS）- 对标签名称进行输入校验和输出转义
4. **中优先级**: AUDIT-004（竞争条件）- 添加并发控制机制

---

*报告生成时间: 2025-01-09*
*审计范围: FEATURE_DESIGN.md, WIKI.md*
