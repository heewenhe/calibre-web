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
**需求**：用户可定义标签到目录的映射规则，系统自动将符合条件的图书文件移动到对应的目录中。

**现状分析**：
- 已存在 helper.update_dir_structure() 用于上传时的目录处理
- 使用 Calibre 的目录结构
- 已存在任务系统可处理后台移动操作

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
│  │  - 文件移动处理                                                        │ │
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
    category = Column(String, default="")  # 分类名称
    description = Column(String, default="")  # 描述
    is_active = Column(Boolean, default=True)  # 是否启用
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

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
    tag_id = Column(Integer, ForeignKey('tags.id'), nullable=False)  # 关联 Calibre Tags 表
```

> **设计说明**：使用关联表 `FileOrgRuleTags` 替代原先的 `tag_ids = Column(JSON)` 方案。关联表通过外键约束保证数据完整性，避免标签被删除后出现悬空引用，同时支持高效的反向查询（查询某个标签被哪些规则使用）。

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
        """添加标签到标签库"""
        pass
        
    def update_tag(self, tag_id, name, category, description):
        """更新标签"""
        pass
        
    def delete_tag(self, tag_id):
        """删除标签"""
        pass
        
    def merge_tags(self, source_tag_ids, target_tag_name):
        """合并标签"""
        pass
        
    def categorize_tags(self, tag_ids, category):
        """批量分类标签"""
        pass
```

#### 4.2.2 cps/services/FileOrganizer.py (新增)
```python
"""
文件组织服务
"""
class FileOrganizerService:
    def get_rules(self):
        """获取所有文件组织规则"""
        pass
        
    def add_rule(self, name, tag_ids, tag_combination, target_directory, priority):
        """添加规则"""
        pass
        
    def update_rule(self, rule_id, **kwargs):
        """更新规则"""
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
        
    def move_book_file(self, book, target_directory):
        """移动图书文件（调用 Calibre 的目录结构处理）"""
        pass
```

#### 4.2.3 cps/tasks/metadata_scan.py (新增)
```python
"""
元数据扫描任务
"""
from cps.services.worker import CalibreTask

class TaskMetadataScan(CalibreTask):
    def __init__(self, provider_id, book_ids=None, user_id=None):
        super().__init__(message=_("Metadata Scan: {}").format(provider_id))
        self.provider_id = provider_id
        self.book_ids = book_ids  # 如果为None则扫描全部
        self.user_id = user_id
        self.scan_history_id = None

    @property
    def name(self):
        return _("Metadata Scan")

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        """执行扫描任务"""
        pass
```

#### 4.2.4 cps/tasks/file_organize.py (新增)
```python
"""
文件组织任务
"""
from cps.services.worker import CalibreTask

class TaskFileOrganize(CalibreTask):
    def __init__(self, rule_ids=None, book_ids=None, user_id=None):
        super().__init__(message=_("File Organization"))
        self.rule_ids = rule_ids  # 要应用的规则
        self.book_ids = book_ids  # 要处理的图书
        self.user_id = user_id

    @property
    def name(self):
        return _("File Organization")

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        """执行文件组织任务"""
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

#### 4.4.2 元数据扫描核心逻辑

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

TagLibrary 表作为 Calibre Tags 表的扩展元数据层，两者通过标签名称关联：

- **读取同步**：TagLibraryService 初始化时，从 Calibre `Tags` 表同步所有标签名称到 TagLibrary（仅新增，不删除）
- **写入同步**：通过 TagLibrary 添加的新标签，同步写入 Calibre `Tags` 表
- **合并同步**：标签合并操作需同时更新 Calibre 数据库中的 `books_tags_link` 关联
- **删除同步**：从 TagLibrary 删除标签时，可选择是否同时从 Calibre Tags 表删除
- **一致性检查**：提供"同步校验"功能，检测两表之间的不一致并修复

#### 4.4.4 与现有任务系统集成
- 在 services/worker.py 中增加对新任务类型的支持
- 在任务列表页面展示元数据扫描和文件组织任务
- 新任务需实现 CalibreTask 的全部抽象方法：`run()`, `name`, `is_cancellable`

#### 4.4.5 与 Calibre 数据库集成
- 使用 calibre_db.session 进行数据库操作
- 使用现有的 Tags 表结构
- 复用 books_tags_link 关联表

#### 4.4.6 Google Drive 兼容性
- 文件组织功能需同时支持本地文件系统和 Google Drive 存储模式
- 复用 `helper.update_dir_structure()` 统一入口，该函数已根据配置自动分发到：
  - `update_dir_structure_file()`：本地文件系统模式
  - `update_dir_structure_gdrive()`：Google Drive 模式
- FileOrganizerService 的 `move_book_file()` 方法应调用 `helper.update_dir_structure()` 而非直接操作文件系统

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
- **风险2**：文件移动的安全性
  - **缓解**：提供预览功能，先模拟再执行，提供撤销机制（或备份）
- **风险3**：大规模扫描的性能问题
  - **缓解**：分批处理，支持增量扫描

---

## 7. 权限控制

利用现有的权限系统：
- 所有管理功能使用 `@admin_required` 装饰器（定义在 `admin.py` 中，检查 `current_user.role_admin()`）
- 管理路由同时使用 `@user_login_required`（来自 `usermanagement.py`）确保用户已登录
- 遵循现有 admin 模块的双重装饰器模式：`@user_login_required` + `@admin_required`
- 导入方式：`from .admin import admin_required`，`from .usermanagement import user_login_required`
- API 响应统一使用 `make_response(jsonify(...))` 模式，与现有 search_metadata.py 等模块保持一致

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
