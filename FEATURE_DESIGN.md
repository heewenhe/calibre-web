# 元数据扫描与文件组织功能 - 需求文档与设计方案

## 1. 需求概述

本功能旨在扩展 Calibre-Web 的元数据处理能力，提供批量元数据扫描、标签库管理和基于标签的文件组织功能。

---

## 2. 需求分析

### 2.1 元数据扫描功能
**需求**：对图书库中的图书进行批量元数据扫描，从豆瓣等现有元数据提供者获取标签信息，并更新到 Calibre 数据库中。

**现状分析**：
- 已存在多个元数据提供者（douban.py、google.py、amazon.py等）
- 已存在元数据搜索接口（search_metadata.py）
- 已存在标签表结构（db.py 中的 Tags 类）
- 已存在任务队列系统（WorkerThread）

### 2.2 标签库管理功能
**需求**：提供可视化的标签库管理界面，支持标签的查看、分类、合并、删除等操作。

**现状分析**：
- 标签表已存在，但缺乏集中管理界面
- 用户已有 allowed_tags 和 denied_tags 配置

### 2.3 基于标签的文件组织功能
**需求**：用户可定义标签到目录的映射规则，系统自动在目标目录下创建软链接（symlink），将符合条件的图书"组织"到自定义目录结构中，而不破坏 Calibre 原有的 `{author}/{title}` 目录结构。

**现状分析**：
- Calibre 目录结构为固定的 `{author}/{title (id)}/` 格式，`book.path` 字段在 [helper.py](file:///workspace/cps/helper.py#L448-L483) 中被硬编码解析为 `author_dir/title_dir` 两部分
- `helper.update_dir_structure()` 是围绕作者/标题变更设计的封闭函数，无法用于任意外部目录移动
- 直接修改 `book.path` 为标签目录会破坏 Calibre 桌面应用的兼容性
- **方案选择**：使用软链接（`os.symlink`）+ 定期同步策略，在独立的标签目录下创建指向原始文件的链接

---

## 3. 设计方案

### 3.1 系统架构

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           Web UI Layer                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────┐ │
│  │  Metadata Scan UI    │  │  Tag Library UI      │  │  File Org UI     │ │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Controller Layer                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  metadata_scheduler.py (新增)                                         │ │
│  │  - 元数据扫描任务调度                                                  │ │
│  │  - 标签批量应用                                                        │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  tag_library.py (新增)                                                │ │
│  │  - 标签CRUD操作                                                        │ │
│  │  - 标签分类管理                                                        │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  file_organizer.py (新增)                                             │ │
│  │  - 标签目录映射                                                        │ │
│  │  - 软链接创建与管理                                                    │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Services Layer                                    │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │  services/Metadata.py        │  │  services/worker.py              │ │
│  │  (现有，可复用)              │  │  (现有，可复用)                  │ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │  services/TagLibrary.py (新) │  │  services/FileOrganizer.py (新)  │ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Data Layer                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  db.py (Calibre 数据库)                                               │ │
│  │  - Books, Tags (现有)                                                 │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  ub.py (用户数据库，新增表)                                            │ │
│  │  - TagLibrary (新增)                                                  │ │
│  │  - FileOrganizationRules (新增)                                       │ │
│  │  - ScanHistory (新增)                                                 │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 详细设计

### 4.1 数据库设计（ub.py 扩展）

新增以下表结构：

#### 4.1.1 TagLibrary 表
```python
class TagLibrary(Base):
    __tablename__ = 'tag_library'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)  # 标签名称
    calibre_tag_id = Column(Integer, unique=True)  # 对应 Calibre Tags.id（非外键，跨数据库）
    category = Column(String, default="")  # 分类名称
    description = Column(String, default="")  # 描述
    is_active = Column(Boolean, default=True)  # 是否启用
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

> **设计说明**：`calibre_tag_id` 不使用 `ForeignKey` 约束，因为 Calibre Tags 表在独立 SQLite 数据库中。关联通过应用层维护：写入时回填 id，一致性检查时验证引用有效性。

#### 4.1.2 FileOrganizationRules 表与标签关联
```python
class FileOrganizationRules(Base):
    __tablename__ = 'file_org_rules'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # 规则名称
    tag_combination = Column(String, default="any")  # "any" 或 "all"
    target_directory = Column(String, nullable=False)  # 目标目录
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)  # 优先级，数值高的优先
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FileOrgRuleTags(Base):
    __tablename__ = 'file_org_rule_tags'
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey('file_org_rules.id'), nullable=False)
    tag_name = Column(String, nullable=False)  # 标签名称，关联 Calibre Tags.name（字符串，非外键）
```

> **设计说明**：使用 `tag_name`（String）而非 `tag_id`（Integer + ForeignKey），因为 Calibre `Tags` 表（db.py）与用户数据库（ub.py）分别使用两个独立的 SQLite 引擎，SQLite 不支持跨数据库外键约束。通过标签名称进行应用层关联：
> - 规则匹配时：通过 `tag_name` 在 Calibre `Tags` 表查找对应标签，再通过 `books_tags_link` 找到图书
> - 标签删除时：在应用层监听并联动清理 `FileOrgRuleTags` 中的对应记录
> - 标签重命名时：需同步更新 `FileOrgRuleTags.tag_name`

#### 4.1.3 ScanHistory 表
```python
class ScanHistory(Base):
    __tablename__ = 'scan_history'
    id = Column(Integer, primary_key=True)
    provider = Column(String, nullable=False)  # 元数据提供者ID (如 "douban")
    total_books = Column(Integer, default=0)
    processed_books = Column(Integer, default=0)
    tags_added = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending, running, success, failed
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    error_log = Column(String, default="")
    user_id = Column(Integer, ForeignKey('user.id'))  # 发起人
```

### 4.2 新增模块设计

#### 4.2.1 cps/services/TagLibrary.py (新增)
```python
"""
标签库管理服务
"""
class TagLibraryService:
    def get_all_tags(self):
        """获取所有标签库标签"""
        pass

    def get_calibre_tags(self):
        """从Calibre数据库获取所有现有标签"""
        pass

    def add_tag(self, name, category="", description=""):
        """添加标签到标签库，同步写入 Calibre Tags 表并回填 calibre_tag_id"""
        pass

    def update_tag(self, tag_id, name, category, description):
        """更新标签，同步更新 Calibre Tags 表和 FileOrgRuleTags.tag_name"""
        pass

    def delete_tag(self, tag_id):
        """删除标签，同步从 Calibre Tags 表删除并清理 books_tags_link 关联"""
        pass

    def merge_tags(self, source_tag_ids, target_tag_name):
        """合并标签，更新 Calibre 数据库中的 books_tags_link 关联"""
        pass

    def categorize_tags(self, tag_ids, category):
        """批量分类标签"""
        pass

    def sync_consistency_check(self):
        """一致性检查：检测 TagLibrary 与 Calibre Tags 表之间的不一致
        返回不一致记录列表，供管理员修复"""
        pass

    def sync_from_calibre(self):
        """从 Calibre Tags 表同步所有标签到 TagLibrary
        以 Tags.id 为主键，标签名称为 Tags.name"""
        pass
```

#### 4.2.2 cps/services/FileOrganizer.py (新增)
```python
"""
文件组织服务
"""
import os
import re
import fcntl


class FileOrganizerService:
    # 允许的目标目录基路径，必须在 Calibre 目录下
    ALLOWED_BASE_DIR = None  # 初始化时从 config.config_calibre_dir 设置
    # 标签名称允许字符：字母、数字、中文、常见标点
    TAG_NAME_PATTERN = re.compile(r'^[\w\s\-\u4e00-\u9fff]+$')
    MAX_TAG_NAME_LENGTH = 100

    def __init__(self, config):
        self.ALLOWED_BASE_DIR = os.path.abspath(config.config_calibre_dir)

    def _validate_target_directory(self, target_directory):
        """校验目标目录必须在允许的基目录下，防止路径遍历攻击"""
        abs_target = os.path.abspath(target_directory)
        abs_base = os.path.abspath(self.ALLOWED_BASE_DIR)
        # 确保目标路径以基目录开头，防止 .. 绕过
        if not os.path.commonpath([abs_target, abs_base]) == abs_base:
            raise ValueError("Target directory must be within the Calibre library directory")
        # 禁止包含 .. 的路径组件
        if '..' in os.path.normpath(target_directory).split(os.sep):
            raise ValueError("Path cannot contain parent directory references")
        return abs_target

    def _validate_tag_name(self, tag_name):
        """校验标签名称，防止 XSS 和注入攻击"""
        if not tag_name or len(tag_name) > self.MAX_TAG_NAME_LENGTH:
            raise ValueError(f"Tag name must be 1-{self.MAX_TAG_NAME_LENGTH} characters")
        if not self.TAG_NAME_PATTERN.match(tag_name):
            raise ValueError("Tag name contains invalid characters")
        return tag_name.strip()

    def get_rules(self):
        """获取所有文件组织规则"""
        pass

    def add_rule(self, name, tag_names, tag_combination, target_directory, priority):
        """添加规则（tag_names 为标签名称列表，对应 Calibre Tags.name）"""
        # 校验目标目录
        self._validate_target_directory(target_directory)
        # 校验所有标签名称
        for tag in tag_names:
            self._validate_tag_name(tag)
        pass

    def update_rule(self, rule_id, **kwargs):
        """更新规则"""
        if 'target_directory' in kwargs:
            self._validate_target_directory(kwargs['target_directory'])
        if 'tag_names' in kwargs:
            for tag in kwargs['tag_names']:
                self._validate_tag_name(tag)
        pass

    def delete_rule(self, rule_id):
        """删除规则"""
        pass

    def apply_rules_to_book(self, book_id):
        """对单个图书应用规则"""
        pass

    def apply_rules_to_all(self):
        """对所有图书应用规则（返回可处理的图书列表）"""
        pass

    def create_symlink(self, book, target_directory):
        """在目标目录下创建软链接（不移动 Calibre 原始文件）
        使用文件锁防止并发竞争条件（TOCTOU）"""
        # 校验目标目录
        safe_target = self._validate_target_directory(target_directory)
        calibre_dir = os.path.join(self.ALLOWED_BASE_DIR, book.path)
        # 确保 calibre_dir 也在基目录下
        self._validate_target_directory(calibre_dir)

        link_name = get_valid_filename(book.title, chars=96) + " (" + str(book.id) + ")"
        link_path = os.path.join(safe_target, link_name)
        # 确保 link_path 也在基目录下
        self._validate_target_directory(link_path)

        # 使用文件锁保护检查和创建操作，防止 TOCTOU 竞争条件
        lock_file = os.path.join(safe_target, '.file_org.lock')
        with open(lock_file, 'w') as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                # 原子操作：先创建临时链接，再重命名
                temp_link = link_path + '.tmp'
                if os.path.islink(temp_link):
                    os.unlink(temp_link)
                os.symlink(calibre_dir, temp_link, target_is_directory=True)
                # 原子重命名替换旧链接
                os.replace(temp_link, link_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
                # 清理临时文件（如果重命名失败）
                if os.path.islink(temp_link):
                    os.unlink(temp_link)

    def clean_stale_links(self, target_directory):
        """清理目标目录中不再匹配规则的死链接"""
        safe_target = self._validate_target_directory(target_directory)
        pass
```

#### 4.2.3 cps/tasks/metadata_scan.py (新增)
```python
"""
元数据扫描任务
"""
import ipaddress
import socket
from urllib.parse import urlparse
from flask_babel import lazy_gettext as N_
from cps.services.worker import CalibreTask


class SSRFProtection:
    """SSRF 防护工具类：限制元数据提供者只能访问允许的域名和 IP"""
    # 允许的元数据提供者域名白名单
    ALLOWED_DOMAINS = {
        'book.douban.com',
        'www.googleapis.com',
        'www.amazon.com',
        'www.amazon.cn',
        'comicvine.gamespot.com',
        'scholar.google.com',
        'lubimyczytac.pl',
    }
    # 禁止访问的私有 IP 段
    PRIVATE_NETWORKS = [
        ipaddress.ip_network('10.0.0.0/8'),
        ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16'),
        ipaddress.ip_network('127.0.0.0/8'),
        ipaddress.ip_network('169.254.0.0/16'),
        ipaddress.ip_network('0.0.0.0/8'),
        ipaddress.ip_network('::1/128'),  # IPv6 loopback
        ipaddress.ip_network('fc00::/7'),  # IPv6 private
        ipaddress.ip_network('fe80::/10'),  # IPv6 link-local
    ]
    # HTTP 请求超时（秒）
    REQUEST_TIMEOUT = 10

    @classmethod
    def validate_url(cls, url):
        """校验 URL 是否允许访问，防止 SSRF 攻击"""
        if not url:
            raise ValueError("URL cannot be empty")
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid URL: no hostname")

        # 检查域名白名单
        if hostname not in cls.ALLOWED_DOMAINS:
            # 也检查子域名
            if not any(hostname.endswith('.' + d) for d in cls.ALLOWED_DOMAINS):
                raise ValueError(f"Domain {hostname} is not in the allowed list")

        # 解析 IP 并检查是否为私有地址
        try:
            ip = ipaddress.ip_address(socket.getaddrinfo(hostname, None)[0][4][0])
            for network in cls.PRIVATE_NETWORKS:
                if ip in network:
                    raise ValueError(f"Access to private IP address {ip} is not allowed")
        except socket.gaierror:
            raise ValueError(f"Cannot resolve hostname: {hostname}")

        return True

    @classmethod
    def get_safe_request_kwargs(cls):
        """获取安全的请求参数（超时等）"""
        return {'timeout': cls.REQUEST_TIMEOUT}


class TaskMetadataScan(CalibreTask):
    def __init__(self, provider_id, book_ids=None, user_id=None):
        super().__init__(message=N_("Metadata Scan: {}").format(provider_id))
        self.provider_id = provider_id
        self.book_ids = book_ids  # 如果为None则扫描全部
        self.user_id = user_id
        self.scan_history_id = None

    @property
    def name(self):
        return N_("Metadata Scan")

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        """执行扫描任务。注意：WorkerThread 在独立线程中运行，访问数据库前必须进入 app_context。"""
        # 在调用 provider 前，校验其请求 URL
        # provider 应在初始化或请求时使用 SSRFProtection.validate_url()
        pass
```

#### 4.2.4 cps/tasks/file_organize.py (新增)
```python
"""
文件组织任务
"""
from flask_babel import lazy_gettext as N_
from cps.services.worker import CalibreTask

class TaskFileOrganize(CalibreTask):
    def __init__(self, rule_ids=None, book_ids=None, user_id=None):
        super().__init__(message=N_("File Organization"))
        self.rule_ids = rule_ids  # 要应用的规则
        self.book_ids = book_ids  # 要处理的图书
        self.user_id = user_id

    @property
    def name(self):
        return N_("File Organization")

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        """执行文件组织任务（创建/更新软链接）。注意：需要进入 app_context。"""
        pass
```

#### 4.2.5 cps/metadata_scheduler.py (新增)
```python
"""
元数据与组织功能 - Web 控制器
"""
from flask import Blueprint, render_template, request, jsonify, make_response
from .cw_login import current_user
from .usermanagement import user_login_required
from .admin import admin_required

metadata_scheduler = Blueprint('metadata_scheduler', __name__)

@metadata_scheduler.route('/admin/metadata_scan', methods=['GET', 'POST'])
@admin_required
def metadata_scan_page():
    """元数据扫描页面"""
    pass

@metadata_scheduler.route('/api/metadata_scan/start', methods=['POST'])
@admin_required
def start_metadata_scan():
    """启动元数据扫描任务"""
    pass

@metadata_scheduler.route('/api/metadata_scan/history', methods=['GET'])
@admin_required
def get_scan_history():
    """获取扫描历史"""
    pass
```

#### 4.2.6 cps/tag_library.py (新增)
```python
"""
标签库管理 - Web 控制器
"""
from flask import Blueprint, render_template, request, jsonify, make_response
from .cw_login import current_user
from .usermanagement import user_login_required
from .admin import admin_required

tag_library = Blueprint('tag_library', __name__)

@tag_library.route('/admin/tag_library', methods=['GET'])
@admin_required
def tag_library_page():
    """标签库管理页面"""
    pass

@tag_library.route('/api/tag_library/tags', methods=['GET', 'POST'])
@admin_required
def tag_library_tags():
    """标签CRUD API"""
    pass
```

#### 4.2.7 cps/file_organizer.py (新增)
```python
"""
文件组织 - Web 控制器
"""
from flask import Blueprint, render_template, request, jsonify, make_response
from .cw_login import current_user
from .usermanagement import user_login_required
from .admin import admin_required

file_organizer = Blueprint('file_organizer', __name__)

@file_organizer.route('/admin/file_organizer', methods=['GET'])
@admin_required
def file_organizer_page():
    """文件组织管理页面"""
    pass

@file_organizer.route('/api/file_organizer/rules', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def file_org_rules():
    """规则CRUD API"""
    pass

@file_organizer.route('/api/file_organizer/apply', methods=['POST'])
@admin_required
def apply_rules():
    """应用规则"""
    pass
```

### 4.3 前端组件设计

> **兼容性注意**：项目使用 Bootstrap **3.4.1**，所有前端组件需使用 Bootstrap 3.x 的 class（如 `col-md-*`、`panel`、`btn-default` 等），不可使用 Bootstrap 4/5 的 class（如 `d-flex`、`form-row`、`btn-outline-*` 等）。

#### 4.3.1 元数据扫描界面 (cps/templates/admin_metadata_scan.html)
- 元数据提供者选择（现有搜索组件可复用）
- 图书范围选择（全部、按标签筛选、按作者筛选）
- 扫描进度展示
- 扫描历史记录

#### 4.3.2 标签库管理界面 (cps/templates/admin_tag_library.html)
- 标签列表展示（支持按分类筛选）
- 标签添加/编辑/删除
- 标签合并功能
- 标签批量操作

#### 4.3.3 文件组织界面 (cps/templates/admin_file_organizer.html)
- 规则列表展示
- 规则创建/编辑/删除
- 规则预览（显示哪些图书会被移动）
- 任务启动

### 4.4 集成设计

#### 4.4.1 与现有元数据系统集成
- 复用现有的 metadata_provider 模块
- 复用 search_metadata.py 中的 provider 发现机制（通过动态扫描 metadata_provider/ 目录自动加载）
- 调用 provider.search() 方法获取元数据，注意返回类型为 `Optional[List[MetaRecord]]`，需处理 None 返回值
- 提取 tags 字段并应用到 Calibre 数据库

**各 Provider 的 tags 返回能力**（经代码审计验证）：
| Provider | 返回 tags | 说明 |
|----------|----------|------|
| 豆瓣 ([douban.py](file:///workspace/cps/metadata_provider/douban.py#L184)) | ✅ | 从 HTML 页面 XPath 提取标签 |
| Google Books ([google.py](file:///workspace/cps/metadata_provider/google.py#L93)) | ✅ | 从 API `categories` 字段映射 |
| Amazon ([amazon.py](file:///workspace/cps/metadata_provider/amazon.py#L84)) | ❌ | 显式设为空列表 `tags=[]` |
| 其他 provider | 待验证 | 需逐个检查实现 |

> **注意**：用户选择 Amazon 作为元数据源时，扫描不会产生任何标签输出，应在 UI 中给出明确提示。

#### 4.4.2 元数据扫描核心逻辑

**Flask 应用上下文**：WorkerThread 在独立线程中执行，访问 `calibre_db.session` 前**必须**显式进入应用上下文（参考 [TaskConvert.run()](file:///workspace/cps/tasks/convert.py#L62-L68)）：
```python
def run(self, worker_thread):
    from cps import app
    with app.app_context():
        # 所有数据库操作在此上下文中执行
        ...
```

**ISBN 批量预加载**：避免 N+1 查询，在扫描前一次性加载所有图书的 ISBN（注意 `Identifiers` 表的关联字段为 `book`，非 `book_id`）：
```python
from sqlalchemy import or_
identifiers = session.query(Identifiers).filter(
    Identifiers.type == "isbn",
    Identifiers.book.in_(all_book_ids)
).all()
# 构建 book_id -> isbn 映射，避免每本书都查一次
isbn_map = {i.book: i.val for i in identifiers}
```

**查询生成策略**：
- 优先使用 ISBN（通过 `Identifiers` 表中 `isbn` 类型的标识符）
- 若无 ISBN，使用 `书名 + 作者` 组合查询
- 使用 `Metadata.get_title_tokens()` 对书名进行分词以提高匹配率

**结果匹配策略**：
- 若搜索返回多个 `MetaRecord`，按以下优先级自动选择最佳匹配：
  1. ISBN 完全匹配
  2. 标题完全匹配 + 作者匹配
  3. 标题相似度最高（Levenshtein 距离）
- 若无高置信度匹配（相似度 < 阈值），跳过该书并记录到 ScanHistory.error_log
- 提供手动审核模式：低置信度匹配暂存待确认，不自动应用

**标签提取与写入策略**：
- 从 `MetaRecord.tags`（`Optional[List[str]]`）提取标签字符串列表
- 对每个标签字符串，在 Calibre `Tags` 表中查找同名标签
  - 若已存在，直接关联到 `books_tags_link`
  - 若不存在，创建新 `Tags` 记录后关联
- 去重逻辑：同一图书不重复关联已有标签

#### 4.4.3 TagLibrary 与 Calibre Tags 同步策略

TagLibrary 表作为 Calibre Tags 表的扩展元数据层，两者通过 `Calibre Tags.id` 关联：

- **读取同步**：TagLibraryService 初始化时，从 Calibre `Tags` 表同步所有标签到 TagLibrary（以 `Tags.id` 为主键，标签名称为 `Tags.name`）
  - `TagLibrary` 新增 `calibre_tag_id` 字段：`Column(Integer, unique=True)`，存储对应 Calibre Tags 的 id
- **写入同步**：通过 TagLibrary 添加的新标签，同步写入 Calibre `Tags` 表，写入后回填 `calibre_tag_id`
- **合并同步**：标签合并操作需更新 Calibre 数据库中的 `books_tags_link` 关联，将旧 tag_id 替换为新 tag_id，再清理旧标签
- **删除同步**：从 TagLibrary 删除标签时，同步从 Calibre Tags 表删除对应标签，同时更新 `books_tags_link`
- **一致性检查**：提供"同步校验"功能，检测 TagLibrary 与 Tags 表之间的不一致（如 TagLibrary 中有 calibre_tag_id 在 Tags 表中已不存在），并提供修复选项
- **同名标签处理**：Calibre Tags 表的 `name` 无唯一约束，同一名称可能存在多条。同步时以 `Tags.id` 为准，在应用层处理名称冲突

**跨数据库事务保障**：
由于 Calibre 数据库（db.py）和用户数据库（ub.py）使用独立的 SQLite 引擎，无法使用数据库级事务保证跨库一致性。采用以下策略：
1. **操作顺序**：先操作 Calibre 数据库（Tags 表），成功后再操作用户数据库（TagLibrary 表）
2. **失败回滚**：若用户数据库操作失败，需回滚 Calibre 数据库的更改（手动删除已插入的记录）
3. **定期校验**：通过 `sync_consistency_check()` 定期检测并修复不一致
4. **应用层封装**：所有跨库操作必须通过 `TagLibraryService` 的统一入口，禁止直接绕过服务层操作单个数据库

#### 4.4.4 与现有任务系统集成
- 在 services/worker.py 中增加对新任务类型的支持
- 在任务列表页面展示元数据扫描和文件组织任务
- 新任务需实现 CalibreTask 的全部抽象方法：`run()`, `name`, `is_cancellable`

#### 4.4.5 与 Calibre 数据库集成
- 使用 calibre_db.session 进行数据库操作
- 使用现有的 Tags 表结构
- 复用 books_tags_link 关联表

#### 4.4.6 文件组织技术方案（软链接 + 定期同步）

**核心约束**：Calibre 目录结构 `{author}/{title (id)}/` 不可变。`book.path` 格式被多处代码硬编码依赖。因此在标签目录下使用软链接而非物理移动。

**目录结构示例**：
```
/calibre_library/                          # Calibre 原始库（不动）
│   Author A/
│       Book Title (123)/
│           book.epub
│           cover.jpg
│           metadata.opf
│
/tag_organized/                            # 标签组织目录（新增，用户配置路径）
│   科幻/
│       Book Title (123) -> ../../../calibre_library/Author A/Book Title (123)/
│   经典/
│       Book Title (123) -> ../../../calibre_library/Author A/Book Title (123)/
```

**同步策略**：
- **全量同步**：遍历所有规则，为每个标签目录清理过期链接并重建
- **增量同步**：标签关联变更时，只更新受影响的标签目录
- **清理逻辑**：移除目标目录中不再匹配任何规则的死链接
- **链接目标**：指向图书的 Calibre 目录（含封面和元数据文件），非单文件

**平台兼容性**：
- **Linux / macOS**：原生支持 `os.symlink()`，目录级链接
- **Windows**：需要管理员权限或启用开发者模式。若权限不足，降级为复制 `.url` 快捷方式文件
- **Google Drive**：不支持软链接，Google Drive 模式下此功能自动禁用，UI 提示不可用

**FileOrganizerService.move_book_file() 更新**：
```python
def create_symlink(self, book, target_directory):
    """在目标目录下创建指向 Calibre 原始目录的软链接"""
    calibre_dir = os.path.join(config.config_calibre_dir, book.path)
    link_name = get_valid_filename(book.title, chars=96) + " (" + str(book.id) + ")"
    link_path = os.path.join(target_directory, link_name)
    # 移除旧链接
    if os.path.islink(link_path):
        os.unlink(link_path)
    os.symlink(calibre_dir, link_path, target_is_directory=True)

def apply_rules_via_symlink(self, rule):
    """根据规则创建软链接"""
    # 1. 解析规则的 tag_name 列表，通过 Calibre Tags 表 + books_tags_link 找到匹配图书
    # 2. 若 tag_combination == "all"，取交集；若 "any"，取并集
    # 3. 按优先级排序规则，高优先级规则先处理
    # 4. 对每本匹配图书，在 target_directory 下创建软链接
```

---

## 5. 实施步骤

### 阶段一：数据库和基础服务
1. 在 ub.py 中添加新表结构
2. 实现 TagLibraryService
3. 实现 FileOrganizerService

### 阶段二：元数据扫描功能
1. 实现 TaskMetadataScan
2. 实现 metadata_scheduler.py 控制器
3. 实现前端界面

### 阶段三：标签库功能
1. 实现 tag_library.py 控制器
2. 实现前端界面

### 阶段四：文件组织功能
1. 实现 TaskFileOrganize
2. 实现 file_organizer.py 控制器
3. 实现前端界面

### 阶段五：集成与测试
1. 在 main.py 中注册新蓝图（注意：蓝图注册在 main.py 而非 __init__.py）
2. 完善权限控制
3. 测试与文档

---

## 6. 依赖与风险

### 6.1 依赖
- 无需新增第三方依赖，使用现有依赖即可
- 可选：需要确保 douban 等元数据提供者的可用性

### 6.2 风险
- **风险1**：豆瓣 API 限制或反爬策略
  - **缓解**：添加请求间隔，支持暂停/恢复，提供其他元数据提供者选项
- **风险2**：软链接的文件系统兼容性
  - **缓解**：Windows 下检测 `os.symlink` 是否可用，不可用时降级为 `.url` 快捷方式；Google Drive 模式下自动禁用本功能并提示
- **风险3**：大规模扫描的性能问题
  - **缓解**：分批处理（每批 50-100 本），支持增量扫描，使用 ISBN 批量预加载避免 N+1 查询
- **风险4**：Calibre 数据库与用户数据库的一致性问题（跨 SQLite 数据库无外键约束）
  - **缓解**：TagLibrary 通过 `calibre_tag_id` 应用层关联 + 一致性检查功能；FileOrgRuleTags 通过 `tag_name` 字符串关联 + 标签重命名/删除时联动更新
- **风险5**：Amazon provider 不返回 tags
  - **缓解**：UI 中明确标注各 provider 的标签支持状态，用户选择时给予提示
- **风险6**：Calibre Tags 表 name 无唯一约束导致同名歧义
  - **缓解**：TagLibrary 以 `Tags.id` 为唯一标识，应用层处理同名冲突并提示用户手动去重

---

## 7. 权限控制

利用现有的权限系统：
- 所有管理功能使用 `@admin_required` 装饰器（定义在 `admin.py` 中，检查 `current_user.role_admin()`）
- 管理路由同时使用 `@user_login_required`（来自 `usermanagement.py`）确保用户已登录
- 遵循现有 admin 模块的双重装饰器模式：`@user_login_required` + `@admin_required`
- 导入方式：`from .admin import admin_required`，`from .usermanagement import user_login_required`
- API 响应统一使用 `make_response(jsonify(...))` 模式，与现有 search_metadata.py 等模块保持一致

**CSRF 防护**：
- 所有状态变更 API（POST/PUT/DELETE）必须启用 CSRF 保护
- 使用 Flask-WTF 提供的 CSRF 令牌验证
- 前端表单和 AJAX 请求必须包含正确的 `X-CSRFToken` 请求头
- 例外：仅 GET 请求可豁免 CSRF 验证

**权限检查清单（代码审查用）**：
| 端点 | 方法 | 所需装饰器 | CSRF |
|------|------|-----------|------|
| `/admin/metadata_scan` | GET/POST | `@user_login_required` + `@admin_required` | ✅ |
| `/api/metadata_scan/start` | POST | `@user_login_required` + `@admin_required` | ✅ |
| `/api/metadata_scan/history` | GET | `@user_login_required` + `@admin_required` | ❌ |
| `/admin/tag_library` | GET | `@user_login_required` + `@admin_required` | ✅ |
| `/api/tag_library/tags` | GET/POST | `@user_login_required` + `@admin_required` | ✅ |
| `/admin/file_organizer` | GET | `@user_login_required` + `@admin_required` | ✅ |
| `/api/file_organizer/rules` | GET/POST/PUT/DELETE | `@user_login_required` + `@admin_required` | ✅ |
| `/api/file_organizer/apply` | POST | `@user_login_required` + `@admin_required` | ✅ |

---

## 8. 文件清单

### 新增文件
```
cps/
├── services/
│   ├── TagLibrary.py          # 标签库服务
│   └── FileOrganizer.py       # 文件组织服务
├── tasks/
│   ├── metadata_scan.py       # 元数据扫描任务
│   └── file_organize.py       # 文件组织任务
├── metadata_scheduler.py      # 元数据扫描控制器
├── tag_library.py             # 标签库控制器
├── file_organizer.py          # 文件组织控制器
├── templates/
│   ├── admin_metadata_scan.html
│   ├── admin_tag_library.html
│   └── admin_file_organizer.html
└── static/
    └── js/
        ├── admin_metadata_scan.js
        ├── admin_tag_library.js
        └── admin_file_organizer.js
```

### 修改文件
```
cps/
├── main.py             # 注册新蓝图（蓝图注册入口，非 __init__.py）
├── ub.py               # 添加新表（TagLibrary, FileOrgRuleTags, FileOrganizationRules, ScanHistory）
├── web.py              # 添加导航链接
├── admin.py            # 添加菜单入口
└── templates/
    └── admin.html      # 管理界面菜单
```

---

## 9. UI 导航结构

在管理界面添加新的菜单项：

```
管理
├── ... (现有)
├── 元数据扫描 (新增)
├── 标签库 (新增)
└── 文件组织 (新增)
```

---

*设计方案完成，可进入开发阶段。*
