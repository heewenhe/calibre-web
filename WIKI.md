# Calibre-Web 项目文档

## 项目概述

Calibre-Web 是一个基于 Flask 的 Web 应用程序，提供了一个简洁直观的界面，用于浏览、阅读和下载存储在 Calibre 数据库中的电子书。

**GitHub**: https://github.com/janeczku/calibre-web
**许可证**: GPL-3.0
**当前版本**: 0.6.27b
**Python 版本**: >= 3.6

---

## 目录结构

```
/workspace/
├── .github/                    # GitHub 配置文件
│   ├── ISSUE_TEMPLATE/         # Issue 模板
│   ├── FUNDING.yml             # 赞助配置
├── cps/                        # 主应用包
│   ├── __init__.py             # 应用工厂，Flask 配置
│   ├── main.py                 # 蓝图注册，应用启动入口
│   ├── web.py                  # 主要 Web 路由和视图
│   ├── db.py                   # Calibre 数据库模型
│   ├── ub.py                   # 用户数据库模型
│   ├── admin.py                # 管理后台
│   ├── shelf.py                # 书架功能
│   ├── opds.py                 # OPDS 协议实现
│   ├── search.py               # 搜索功能
│   ├── search_metadata.py      # 元数据搜索（含 provider 自动发现）
│   ├── editbooks.py            # 图书编辑
│   ├── uploader.py             # 上传功能
│   ├── converter.py            # 格式转换
│   ├── kobo.py                 # Kobo 设备同步
│   ├── kobo_auth.py            # Kobo 认证
│   ├── gdrive.py               # Google Drive 集成
│   ├── oauth_bb.py             # OAuth 认证
│   ├── remotelogin.py          # 远程登录（Magic Link）
│   ├── basic.py                # 基础路由
│   ├── about.py                # 关于页面
│   ├── schedule.py             # 定时任务管理
│   ├── tasks_status.py         # 任务状态查询
│   ├── usermanagement.py       # 用户管理与权限装饰器
│   ├── helper.py               # 通用工具函数
│   ├── constants.py            # 常量定义
│   ├── config_sql.py           # 数据库配置
│   ├── render_template.py      # 模板渲染工具
│   ├── jinjia.py               # Jinja2 过滤器
│   ├── error_handler.py        # 错误处理
│   ├── string_helper.py        # 字符串工具
│   ├── cli.py                  # 命令行接口
│   ├── server.py               # Web 服务器管理
│   ├── cw_login/               # 登录认证模块
│   │   ├── login_manager.py    # Flask-Login 管理器
│   │   ├── config.py
│   │   ├── mixins.py
│   │   ├── signals.py
│   │   └── utils.py
│   ├── services/               # 后台服务
│   │   ├── Metadata.py         # 元数据服务基类
│   │   ├── background_scheduler.py  # 定时任务调度器
│   │   ├── worker.py           # 后台工作线程与 CalibreTask 基类
│   │   ├── gmail.py            # Gmail 发送服务
│   │   ├── goodreads_support.py # Goodreads 支持
│   │   └── simpleldap.py       # LDAP 认证
│   ├── metadata_provider/      # 元数据来源提供者（自动发现加载）
│   │   ├── google.py           # Google Books
│   │   ├── amazon.py           # Amazon
│   │   ├── douban.py           # 豆瓣
│   │   ├── comicvine.py        # ComicVine
│   │   ├── scholar.py          # 学术论文
│   │   └── lubimyczytac.py     # 波兰阅读平台
│   ├── cw_advocate/           # HTTP 连接管理
│   ├── tasks/                  # 后台任务
│   │   ├── convert.py          # 转换任务
│   │   ├── upload.py           # 上传任务
│   │   ├── thumbnail.py        # 缩略图生成
│   │   ├── clean.py            # 清理任务
│   │   ├── database.py         # 数据库任务
│   │   └── mail.py             # 邮件任务
│   ├── static/                 # 静态资源
│   │   ├── css/                # 样式表
│   │   ├── js/                 # JavaScript
│   │   ├── img/                # 图片资源
│   │   ├── cmaps/              # PDF 字符映射
│   │   └── locale/             # PDF 阅读器本地化
│   ├── templates/              # Jinja2 模板
│   └── translations/           # 国际化翻译文件
│       ├── zh_Hans_CN/         # 简体中文
│       ├── zh_Hant_TW/         # 繁体中文
│       ├── en-US/              # 英语
│       └── ... (20+ 语言)
├── test/                       # 测试文件
├── cps.py                      # 应用入口
├── requirements.txt            # 依赖列表
├── pyproject.toml              # 项目配置
└── README.md                   # 项目说明
```

---

## 核心模块详解

### 1. 应用初始化 (`cps/__init__.py`)

应用使用 Flask 框架构建，主要功能：

- **Flask 应用创建**: 配置会话管理、CSRF 保护、速率限制
- **登录管理器**: 自定义 `MyLoginManager` 继承 Flask-Login
- **数据库初始化**: Calibre 数据库和用户数据库的初始化
- **国际化支持**: Flask-Babel 多语言支持
- **后台服务**: LDAP、Goodreads 等可选服务初始化
- **定时任务**: APScheduler 定时任务注册

**关键配置**:
```python
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Lax'
REMEMBER_COOKIE_SAMESITE='Strict'
```

### 2. Web 路由 (`cps/web.py`)

主要 Blueprint，处理所有 Web 请求：

- **首页路由**: `/`, `/index`
- **图书详情**: `/book/<book_id>`
- **阅读器**: `/read/<book_id>`, `/readpdf`, `/readtxt`
- **搜索**: `/search`, `/advanced_search`
- **作者/出版社**: `/author`, `/publisher`
- **书架**: `/shelves`, `/shelf/<shelf_id>`
- **用户管理**: `/admin`, `/user/<user_id>`
- **OPDS**: `/opds`

### 3. Calibre 数据库模型 (`cps/db.py`)

使用 SQLAlchemy ORM 映射 Calibre 数据库表：

**核心表**:
- `Books`: 图书信息
- `Authors`: 作者
- `Tags`: 标签
- `Series`: 系列
- `Ratings`: 评分
- `Languages`: 语言
- `Publishers`: 出版社
- `Identifiers`: 标识符 (ISBN, DOI 等)

**关联表**:
- `books_authors_link`: 图书-作者关联
- `books_tags_link`: 图书-标签关联
- `books_series_link`: 图书-系列关联
- `books_ratings_link`: 图书-评分关联
- `books_languages_link`: 图书-语言关联
- `books_publishers_link`: 图书-出版社关联

### 4. 用户数据库模型 (`cps/ub.py`)

Calibre-Web 独立用户数据库，使用 SQLite：

**核心表**:
- `User`: 用户信息
- `User_Sessions`: 用户会话
- `ReadBook`: 用户-图书关联（阅读状态、进度，表名 `book_read_link`）
- `Shelf`: 书架
- `BookShelf`: 书架-图书关联（表名 `book_shelf_link`）
- `Bookmark`: 书签
- `Downloads`: 下载记录
- `ArchivedBook`: 归档图书
- `ShelfArchive`: 归档书架
- `Registration`: 注册信息
- `RemoteAuthToken`: 远程认证令牌
- `Thumbnail`: 缩略图

**Kobo 相关表**:
- `KoboReadingState`: Kobo 阅读状态
- `KoboBookmark`: Kobo 书签
- `KoboStatistics`: Kobo 统计
- `KoboSyncedBooks`: Kobo 同步图书

**用户角色** (`constants.py`):
```python
ROLE_USER = 0          # 普通用户
ROLE_ADMIN = 1 << 0     # 管理员
ROLE_DOWNLOAD = 1 << 1 # 下载权限
ROLE_UPLOAD = 1 << 2   # 上传权限
ROLE_EDIT = 1 << 3     # 编辑权限
ROLE_PASSWD = 1 << 4   # 修改密码权限
ROLE_ANONYMOUS = 1 << 5 # 匿名用户
ROLE_EDIT_SHELFS = 1 << 6 # 编辑书架权限
ROLE_DELETE_BOOKS = 1 << 7 # 删除图书权限
ROLE_VIEWER = 1 << 8   # 阅读器权限
```

### 5. 元数据服务 (`cps/services/Metadata.py`)

抽象基类，定义元数据搜索接口：

```python
class Metadata:
    __name__ = "Generic"
    __id__ = "generic"
    
    def search(self, query, generic_cover, locale) -> Optional[List[MetaRecord]]
    def get_title_tokens(self, title, strip_joiners=True) -> Generator[str, None, None]
```

**支持的元数据来源**:
| 来源 | 文件 | 功能 |
|------|------|------|
| Google Books | `google.py` | 搜索图书信息 |
| Amazon | `amazon.py` | 搜索图书信息 |
| 豆瓣 | `douban.py` | 中文图书搜索 |
| ComicVine | `comicvine.py` | 漫画搜索 |
| Scholar | `scholar.py` | 学术论文搜索 |
| LubimyCzytac | `lubimyczytac.py` | 波兰语图书搜索 |

### 6. OPDS 协议 (`cps/opds.py`)

实现 OPDS (Open Publication Distribution System) 协议，支持电子书阅读器应用：

- 浏览目录
- 搜索结果
- 下载图书
- 阅读进度同步

### 7. Kobo 同步 (`cps/kobo.py`)

支持 Kobo 电子书阅读器的同步功能：

- 设备发现
- 书架同步
- 阅读进度同步
- kepub 格式支持

### 8. Google Drive 集成 (`cps/gdrive.py`)

允许将 Calibre 图书馆存储在 Google Drive 上：

- OAuth 认证
- 文件同步
- 远程访问

---

## 支持的文件格式

### 上传格式 (`EXTENSIONS_UPLOAD`)
```
txt, pdf, epub, kepub, mobi, azw, azw3, 
cbr, cbz, cbt, cb7, djvu, djv, prc, doc, docx, 
fb2, html, rtf, lit, odt, mp3, mp4, ogg, opus, 
wav, flac, m4a, m4b
```

### 可转换格式
**源格式** (`EXTENSIONS_CONVERT_FROM`):
```
pdf, epub, mobi, azw3, docx, rtf, fb2, lit, lrf, 
txt, htmlz, odt, cbz, cbr, prc
```

**目标格式** (`EXTENSIONS_CONVERT_TO`):
```
pdf, epub, mobi, azw3, docx, rtf, fb2, lit, lrf, 
txt, htmlz, odt
```

### 音频格式 (`EXTENSIONS_AUDIO`)
```
mp3, mp4, ogg, opus, wav, flac, m4a, m4b
```

---

## 可选依赖功能

### LDAP 认证 (`ldap`)
```
python-ldap>=3.0.0,<3.5.0
Flask-SimpleLDAP>=1.4.0,<2.2.0
```

### OAuth 认证 (`oauth`)
```
Flask-Dance>=2.0.0,<7.2.0
SQLAlchemy-Utils>=0.33.5,<0.43.0
```

### Google Drive (`gdrive`)
```
google-api-python-client>=2.73.00,<2.200.0
gevent>20.6.0,<25.9.2
greenlet>=0.4.17,<3.4.0
httplib2>=0.9.2,<0.32.0
PyDrive2>=1.15.0,<1.22.0
oauth2client>=4.0.0,<4.1.4
uritemplate>=3.0.0,<4.3.0
pyasn1-modules>=0.0.8,<0.7.0
pyasn1>=0.1.9,<0.7.0
PyYAML>=3.12,<6.1
rsa>=3.4.2,<4.10.0
```

### Goodreads (`goodreads`)
```
goodreads>=0.3.2,<0.4.0
python-Levenshtein>=0.12.0,<0.28.0
```

### Gmail 发送 (`gmail`)
```
google-auth-oauthlib>=1.0.0,<1.3.0
google-api-python-client>=2.73.00,<2.200.0
```

### 元数据搜索 (`metadata`)
```
rarfile>=3.2,<5.0
scholarly>=1.2.0,<1.8
markdown2>=2.0.0,<2.6.0
html2text>=2020.1.16,<2025.4.16
python-dateutil>=2.1,<2.10.0
beautifulsoup4>=4.0.1,<4.15.0
faust-cchardet>=2.1.18,<2.1.20
py7zr>=0.15.0,<0.21.0
mutagen>=1.40.0,<1.50.0
pycountry>=20.0.0,<25.0.0
```

### 漫画支持 (`comics`)
```
natsort>=2.2.0,<8.5.0
comicapi>=2.2.0,<3.3.0
```

### Kobo 支持 (`kobo`)
```
jsonschema>=3.2.0,<4.30.0
```

---

## 前端技术栈

### CSS 框架
- Bootstrap 3.x
- 自定义样式 (`main.css`, `style.css`, `caliBlur.css`)

### JavaScript 库
- jQuery
- Bootstrap Table
- TinyMCE (富文本编辑器)
- PDF.js (PDF 阅读)
- epub.js (EPUB 阅读)
- DjVu.js (DjVu 阅读)

### 阅读器支持
- **PDF**: 使用 PDF.js
- **EPUB**: 使用 epub.js
- **DjVu**: 使用 DjVu.js
- **纯文本**: 内置阅读器
- **漫画 (CBZ/CBR)**: 使用 kthoom.js

---

## 国际化

支持 20+ 种语言，翻译文件位于 `cps/translations/`：

| 语言 | 目录 |
|------|------|
| 简体中文 | `zh_Hans_CN` |
| 繁体中文 | `zh_Hant_TW` |
| 英语 | `en-US` |
| 德语 | `de` |
| 法语 | `fr` |
| 西班牙语 | `es` |
| 日语 | `ja` |
| 韩语 | `ko` |
| 俄语 | `ru` |
| ... | ... |

---

## 配置文件

### 环境变量
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CALIBRE_PORT` | 服务端口 | `8083` |
| `CALIBRE_DBPATH` | Calibre 数据库路径 | 当前目录 |
| `CALIBRE_RECONNECT` | 数据库重连超时 | - |
| `CALIBRE_LOCALHOST` | 限制仅本地访问 | - |
| `CALIBRE_UNIX_SOCKET` | Unix Socket 路径 | - |
| `FLASK_DEBUG` | 调试模式 | - |
| `SECRET_KEY` | Flask 密钥 | 自动生成 |
| `COOKIE_PREFIX` | Cookie 前缀 | - |

### 配置文件位置
- 通过 pip 安装: `~/.calibre-web/`
- 开发模式: 项目根目录

---

## API 路由

### 主要路由
| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 首页 |
| `/book/<id>` | GET | 图书详情 |
| `/read/<id>` | GET | 阅读器 |
| `/search` | GET/POST | 搜索 |
| `/opds` | GET | OPDS 订阅 |
| `/admin` | GET | 管理后台 |
| `/api/v1/books` | GET/POST | REST API |

### API 端点
| 端点 | 说明 |
|------|------|
| `/ajax/bookpopup` | 获取图书弹窗 |
| `/ajax/emailstat` | 邮件状态 |
| `/ajax/toggleread` | 切换阅读状态 |
| `/api/v1/books` | 图书 CRUD |
| `/api/v1/shelves` | 书架 CRUD |

---

## 安全性

### 安全头部
```python
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000
Content-Security-Policy: (动态配置)
```

### 认证方式
1. **本地认证**: 用户名/密码
2. **LDAP 认证**: 企业目录
3. **OAuth 认证**: Google/GitHub
4. **代理认证**: 反向代理头部
5. **Magic Link**: 电子书阅读器专用链接

### 速率限制
使用 Flask-Limiter，默认启用但无默认速率限制。登录和注册接口配置了明确的限制（40次/天、3次/分钟）。可配置 Redis/Memcached 作为存储后端（通过 `RATELIMIT_STORAGE_URI` 配置）。

---

## 定时任务

使用 APScheduler 管理后台任务：

| 任务 | 说明 | 默认间隔 |
|------|------|----------|
| Goodreads 同步 | 同步阅读进度 | 可配置 |
| Gmail 发送 | 发送电子书到邮箱 | 按需 |
| 元数据备份 | 备份元数据 | 可配置 |
| 缓存清理 | 清理过期缓存 | 可配置 |

---

## 部署方式

### 1. pip 安装 (推荐)
```bash
python3 -m venv calibre-web-env
source calibre-web-env/bin/activate
pip install calibreweb
cps
```

### 2. Docker
```bash
docker run -d \
  -p 8083:8083 \
  -v /path/to/calibre/library:/library \
  linuxserver/calibre-web
```

### 3. 手动部署
```bash
git clone https://github.com/janeczku/calibre-web.git
cd calibre-web
pip install -r requirements.txt
python cps.py
```

---

## 开发指南

### 项目结构
- `cps/`: 主应用包
- `cps/static/`: 静态资源
- `cps/templates/`: 模板文件
- `cps/services/`: 后台服务
- `cps/metadata_provider/`: 元数据来源

### 关键约定
- 使用 Flask Blueprint 组织路由
- 使用 SQLAlchemy ORM
- 使用 Jinja2 模板引擎
- 使用 Flask-Babel 国际化
- 使用 APScheduler 定时任务

### 添加新的元数据源
1. 继承 `Metadata` 基类
2. 实现 `search()` 方法
3. 返回 `Optional[List[MetaRecord]]` 列表
4. 将实现文件放入 `metadata_provider/` 目录，系统会通过 `search_metadata.py` 自动发现并加载

### 添加新的翻译
1. 在 `cps/translations/` 创建语言目录
2. 创建 `LC_MESSAGES/messages.po`
3. 使用 `pybabel` 提取和编译翻译

---

## 常见问题

### 端口冲突
```bash
export CALIBRE_PORT=8084
cps
```

### 使用 Google Drive
1. 配置 OAuth 凭据
2. 在管理界面启用 GDrive
3. 设置图书馆路径

### 电子书阅读器支持
使用 OPDS 协议，在阅读器中添加订阅地址：
```
http://your-server:8083/opds
```

---

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

请参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详情。

---

## 许可证

本项目基于 GPL-3.0 许可证开源，详见 [LICENSE](LICENSE) 文件。

---

*本文档由代码自动分析生成，如有疏漏请提交 Issue。*
