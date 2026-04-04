# 虚拟文件系统设计文档

## 1. 背景与目标

### 1.1 问题

当前 cubebox 的文件管理存在以下问题：

1. **Sandbox 文件是临时的**：文件存在于 sandbox 容器内，sandbox 被回收后文件丢失
2. **无统一文件存储抽象**：skill 文件用本地 FS，sandbox 文件用容器 API，无法统一管理
3. **缺少语义检索能力**：agent 无法基于文件内容的语义进行搜索和关联
4. **无文件持久化**：用户上传的文件、agent 产出的制品（artifacts）无持久化方案

### 1.2 目标

设计一个虚拟文件系统（VFS），为 sandbox 中的文件操作提供持久化支持：

- **Blob Storage**：存储文件的原始内容（二进制）
- **Embedding Storage**：存储文件内容的向量表示，支持语义检索
- **可插拔后端**：支持不同的存储实现（本地 FS、S3/OSS、数据库等）
- **与 Sandbox 集成**：sandbox 生命周期中自动同步文件到 VFS

## 2. 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    Agent / API Layer                 │
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │  Sandbox     │  │  Tools      │  │  API Routes │ │
│  │  Middleware   │  │  (file ops) │  │  (upload/dl)│ │
│  └──────┬───────┘  └──────┬──────┘  └──────┬──────┘ │
│         │                 │                │         │
│  ┌──────▼─────────────────▼────────────────▼──────┐ │
│  │            VirtualFileSystem (VFS)              │ │
│  │                                                 │ │
│  │  ┌────────────┐  ┌──────────┐  ┌────────────┐  │ │
│  │  │ FileIndex   │  │ BlobStore│  │ Embedding  │  │ │
│  │  │ (metadata)  │  │ (content)│  │ Store      │  │ │
│  │  └─────┬──────┘  └────┬─────┘  └─────┬──────┘  │ │
│  └────────┼──────────────┼───────────────┼─────────┘ │
└───────────┼──────────────┼───────────────┼───────────┘
            │              │               │
     ┌──────▼──────┐ ┌────▼────┐ ┌────────▼────────┐
     │   MySQL /   │ │ Local   │ │  FAISS / Milvus │
     │   SQLite    │ │ FS / S3 │ │  / Qdrant / PG  │
     └─────────────┘ └─────────┘ └─────────────────┘
```

### 2.1 三层职责划分

| 层 | 职责 | 接口 |
|---|---|---|
| **FileIndex** | 文件元数据管理（路径、大小、类型、归属关系） | CRUD on metadata |
| **BlobStore** | 文件内容的二进制存储（content-addressed） | `put(data) -> hash`, `get(hash) -> data` |
| **EmbeddingStore** | 文件内容的向量存储与检索 | `upsert(file_id, vectors)`, `search(query, top_k)` |

## 3. 核心数据模型

### 3.1 FileNode（文件索引）

```python
class FileNode(SQLModel, table=True):
    """虚拟文件系统的文件/目录节点。"""

    __tablename__ = "vfs_file_nodes"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)

    # 归属
    workspace_id: str = Field(max_length=64, index=True)  # 隔离维度（用户/会话/项目）
    sandbox_id: str | None = Field(default=None, max_length=255)  # 关联的 sandbox

    # 路径
    path: str = Field(max_length=4096)               # 虚拟绝对路径，如 /workspace/main.py
    name: str = Field(max_length=255)                 # 文件名
    is_dir: bool = Field(default=False)               # 是否为目录

    # 内容引用（目录无此字段）
    blob_hash: str | None = Field(default=None, max_length=128)  # content hash → BlobStore
    size_bytes: int = Field(default=0)
    mime_type: str | None = Field(default=None, max_length=128)

    # 向量索引状态
    embedding_status: str = Field(default="pending", max_length=20)
    # pending / indexed / skipped / error

    # 元数据
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**唯一约束**：`(workspace_id, path)` — 同一 workspace 下路径唯一。

### 3.2 Blob（内容块）

```python
class Blob(SQLModel, table=True):
    """Content-addressed 文件内容存储元数据。"""

    __tablename__ = "vfs_blobs"

    hash: str = Field(primary_key=True, max_length=128)  # SHA-256
    size_bytes: int
    ref_count: int = Field(default=1)                     # 引用计数，用于 GC
    storage_backend: str = Field(max_length=32)           # "local" | "s3" | "db"
    storage_key: str = Field(max_length=1024)             # 后端特定的存储路径/key
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**去重**：相同内容只存一份（content-addressed by SHA-256）。

## 4. 接口设计

### 4.1 BlobStore（文件内容存储）

```python
class BlobStore(ABC):
    """Content-addressed binary storage."""

    @abstractmethod
    async def put(self, data: bytes) -> str:
        """存储数据，返回 content hash (SHA-256)。如果已存在则跳过写入。"""
        ...

    @abstractmethod
    async def get(self, blob_hash: str) -> bytes | None:
        """根据 hash 读取数据，不存在返回 None。"""
        ...

    @abstractmethod
    async def delete(self, blob_hash: str) -> bool:
        """删除数据。返回是否成功。"""
        ...

    @abstractmethod
    async def exists(self, blob_hash: str) -> bool:
        """检查数据是否存在。"""
        ...
```

**内置实现**：

| 实现 | 场景 | 存储位置 |
|---|---|---|
| `LocalBlobStore` | 开发环境 | 本地文件系统，路径为 `{base_dir}/{hash[:2]}/{hash[2:4]}/{hash}` |
| `S3BlobStore` | 生产环境 | S3/OSS/MinIO，key 为 `blobs/{hash[:2]}/{hash[2:4]}/{hash}` |
| `DBBlobStore` | 小文件/简单部署 | MySQL LONGBLOB，适合 <10MB 文件 |

### 4.2 EmbeddingStore（向量存储）

```python
class EmbeddingStore(ABC):
    """向量存储与语义检索。"""

    @abstractmethod
    async def upsert(
        self,
        file_id: str,
        chunks: list[EmbeddingChunk],
    ) -> None:
        """为文件写入/更新向量。一个文件可以有多个 chunk。"""
        ...

    @abstractmethod
    async def delete(self, file_id: str) -> None:
        """删除文件的所有向量。"""
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        workspace_id: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """在 workspace 范围内做语义检索。"""
        ...


@dataclass
class EmbeddingChunk:
    chunk_index: int
    text: str              # 原始文本片段
    vector: list[float]    # 向量
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    file_id: str
    chunk_index: int
    score: float
    text: str
    metadata: dict[str, Any]
```

**内置实现**：

| 实现 | 场景 | 依赖 |
|---|---|---|
| `FaissEmbeddingStore` | 开发/小规模 | faiss-cpu，内存向量库 + 持久化到磁盘 |
| `MilvusEmbeddingStore` | 生产 | Milvus/Zilliz，分布式向量数据库 |
| `PGVectorEmbeddingStore` | 简单生产 | PostgreSQL + pgvector 扩展 |

### 4.3 VirtualFileSystem（对外统一接口）

```python
class VirtualFileSystem:
    """统一文件操作入口，编排 FileIndex + BlobStore + EmbeddingStore。"""

    def __init__(
        self,
        session: AsyncSession,
        blob_store: BlobStore,
        embedding_store: EmbeddingStore | None = None,
        embedder: Embedder | None = None,
    ) -> None: ...

    # ── 文件操作 ──────────────────────────────────────────

    async def write_file(
        self,
        workspace_id: str,
        path: str,
        data: bytes,
        mime_type: str | None = None,
    ) -> FileNode:
        """写入文件：存 blob → 更新 FileIndex → 异步触发 embedding。"""
        ...

    async def read_file(self, workspace_id: str, path: str) -> bytes | None:
        """读取文件内容。"""
        ...

    async def delete_file(self, workspace_id: str, path: str) -> bool:
        """删除文件：更新 FileIndex → blob ref_count-- → 删 embedding。"""
        ...

    async def list_dir(
        self,
        workspace_id: str,
        path: str = "/",
        recursive: bool = False,
    ) -> list[FileNode]:
        """列出目录内容。"""
        ...

    async def stat(self, workspace_id: str, path: str) -> FileNode | None:
        """获取文件/目录元数据。"""
        ...

    async def move(
        self,
        workspace_id: str,
        src: str,
        dst: str,
    ) -> FileNode:
        """移动/重命名文件。"""
        ...

    # ── 批量操作（sandbox 同步用） ────────────────────────

    async def sync_from_sandbox(
        self,
        workspace_id: str,
        sandbox: Sandbox,
        paths: list[str],
    ) -> list[FileNode]:
        """从 sandbox 批量拉取文件到 VFS。"""
        ...

    async def sync_to_sandbox(
        self,
        workspace_id: str,
        sandbox: Sandbox,
        paths: list[str],
    ) -> None:
        """从 VFS 推送文件到 sandbox。"""
        ...

    # ── 语义检索 ──────────────────────────────────────────

    async def semantic_search(
        self,
        workspace_id: str,
        query: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """根据自然语言查询，在 workspace 文件中做语义检索。"""
        ...
```

## 5. 存储后端注册机制

采用 **工厂 + 配置驱动** 的方式，与现有 `LLMFactory` 模式一致：

```python
# cubebox/vfs/factory.py

_blob_store_registry: dict[str, type[BlobStore]] = {}
_embedding_store_registry: dict[str, type[EmbeddingStore]] = {}


def register_blob_store(name: str, cls: type[BlobStore]) -> None:
    _blob_store_registry[name] = cls


def register_embedding_store(name: str, cls: type[EmbeddingStore]) -> None:
    _embedding_store_registry[name] = cls


def create_blob_store(config: dict[str, Any]) -> BlobStore:
    """根据 config 创建 BlobStore 实例。"""
    backend = config["backend"]  # "local" | "s3" | "db"
    cls = _blob_store_registry[backend]
    return cls(**config.get("options", {}))


def create_embedding_store(config: dict[str, Any]) -> EmbeddingStore:
    """根据 config 创建 EmbeddingStore 实例。"""
    backend = config["backend"]  # "faiss" | "milvus" | "pgvector"
    cls = _embedding_store_registry[backend]
    return cls(**config.get("options", {}))
```

### 5.1 配置示例 (`config.yaml`)

```yaml
vfs:
  enabled: true

  blob_store:
    backend: "local"            # local | s3 | db
    options:
      base_dir: "./data/blobs"  # LocalBlobStore 专用

  # S3 示例:
  # blob_store:
  #   backend: "s3"
  #   options:
  #     bucket: "cubebox-blobs"
  #     prefix: "blobs/"
  #     region: "cn-beijing"
  #     endpoint_url: "https://oss-cn-beijing.aliyuncs.com"  # 阿里云 OSS 兼容

  embedding_store:
    enabled: true
    backend: "faiss"            # faiss | milvus | pgvector
    options:
      index_dir: "./data/embeddings"
      dimension: 1536

  # Milvus 示例:
  # embedding_store:
  #   enabled: true
  #   backend: "milvus"
  #   options:
  #     uri: "http://localhost:19530"
  #     collection: "cubebox_vfs"
  #     dimension: 1536

  embedder:
    model: "text-embedding-3-small"  # 用于生成向量的模型
    chunk_size: 1000                 # 文本分块大小
    chunk_overlap: 200               # 分块重叠

  sync:
    auto_sync: true                  # sandbox 回收时自动同步
    watch_paths:                     # 需要同步的 sandbox 路径
      - "/workspace"
      - "/home/user"
    ignore_patterns:                 # 忽略的文件模式
      - "*.pyc"
      - "__pycache__"
      - ".git"
      - "node_modules"
    max_file_size: 10485760          # 单文件最大 10MB
```

## 6. Sandbox 集成

### 6.1 文件同步流程

```
Sandbox 创建                          Sandbox 回收
    │                                      │
    ▼                                      ▼
sync_to_sandbox()                    sync_from_sandbox()
    │                                      │
    ├─ VFS.list_dir(workspace)             ├─ sandbox.download(watch_paths)
    ├─ VFS.read_file() × N                 ├─ VFS.write_file() × N
    └─ sandbox.upload(files)               └─ 触发 embedding 异步任务
```

### 6.2 SandboxManager 改造

在现有 `SandboxManager` 中增加 VFS 同步钩子：

```python
class SandboxManager:
    def __init__(self, ..., vfs: VirtualFileSystem | None = None):
        self._vfs = vfs

    async def get_or_create(self, user_id: str, ...) -> Sandbox:
        sandbox = await self._create_sandbox(...)

        # 新增：从 VFS 恢复上次的工作区文件
        if self._vfs:
            await self._vfs.sync_to_sandbox(
                workspace_id=user_id,
                sandbox=sandbox,
                paths=config.get("vfs.sync.watch_paths", ["/workspace"]),
            )
        return sandbox

    async def _on_sandbox_expire(self, sandbox_id: str, user_id: str) -> None:
        # 新增：sandbox 回收前，同步文件到 VFS
        if self._vfs:
            sandbox = self._get_sandbox(sandbox_id)
            await self._vfs.sync_from_sandbox(
                workspace_id=user_id,
                sandbox=sandbox,
                paths=config.get("vfs.sync.watch_paths", ["/workspace"]),
            )
```

## 7. Embedding 处理流程

### 7.1 异步 Embedding Pipeline

文件写入后，embedding 不阻塞主流程，异步执行：

```
write_file()
    │
    ├─ 同步：存 blob + 更新 FileIndex (embedding_status="pending")
    │
    └─ 异步：EmbeddingPipeline.enqueue(file_id)
              │
              ├─ 读取文件内容
              ├─ 判断是否可索引（text/code 类型，大小限制）
              │   ├─ 不可索引 → embedding_status="skipped"
              │   └─ 可索引 ↓
              ├─ 文本分块 (chunking)
              ├─ 调用 Embedder 模型生成向量
              ├─ EmbeddingStore.upsert(file_id, chunks)
              └─ 更新 FileIndex embedding_status="indexed"
```

### 7.2 Embedder 接口

```python
class Embedder(ABC):
    """文本向量化接口。"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化。"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""
        ...
```

复用现有 `LLMFactory` 的 provider 机制，默认使用 OpenAI embedding API。

## 8. 包结构

```
cubebox/vfs/
├── __init__.py             # init_vfs() / get_vfs() 单例管理
├── core.py                 # VirtualFileSystem 类
├── models.py               # FileNode, Blob SQLModel
├── factory.py              # 工厂 + 注册表
├── embedder.py             # Embedder ABC + OpenAIEmbedder
├── pipeline.py             # EmbeddingPipeline（异步队列）
├── blob/
│   ├── __init__.py
│   ├── base.py             # BlobStore ABC
│   ├── local.py            # LocalBlobStore
│   ├── s3.py               # S3BlobStore
│   └── db.py               # DBBlobStore
└── embedding/
    ├── __init__.py
    ├── base.py             # EmbeddingStore ABC
    ├── faiss.py            # FaissEmbeddingStore
    ├── milvus.py           # MilvusEmbeddingStore
    └── pgvector.py         # PGVectorEmbeddingStore
```

## 9. 实现分期

### Phase 1：核心框架（MVP）

- FileNode + Blob 数据模型 + Alembic 迁移
- BlobStore ABC + `LocalBlobStore` 实现
- VirtualFileSystem 核心操作：`write_file`, `read_file`, `delete_file`, `list_dir`
- 基础配置接入 `config.yaml`
- 单元测试

### Phase 2：Sandbox 集成

- SandboxManager 增加 VFS 同步钩子
- `sync_from_sandbox` / `sync_to_sandbox` 实现
- 文件变更检测（基于 hash 判断是否需要同步）
- 新增 Agent tool：`vfs_search`（按路径搜索文件）

### Phase 3：Embedding 能力

- EmbeddingStore ABC + `FaissEmbeddingStore` 实现
- Embedder + 异步 pipeline
- VFS `semantic_search` 集成
- 新增 Agent tool：`semantic_search`

### Phase 4：生产存储后端

- `S3BlobStore`（支持 AWS S3 / 阿里云 OSS）
- `MilvusEmbeddingStore`
- Blob GC（ref_count 降为 0 时清理）
- 监控指标（存储用量、向量数量、检索延迟）

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Content Addressing | SHA-256 hash | 自动去重，跨 workspace 复用内容 |
| 元数据存储 | MySQL (SQLModel) | 与现有 DB 一致，事务性保证 |
| Blob 默认后端 | LocalFS | 开发简单，生产切 S3 只改配置 |
| Embedding 默认后端 | FAISS | 零依赖（pip install），小规模够用 |
| 异步 Embedding | asyncio.Queue | 不阻塞文件写入，与现有事件循环共用 |
| 隔离维度 | workspace_id | 灵活映射到 user_id / project_id / conversation_id |
| 路径模型 | 虚拟绝对路径 | 不依赖真实文件系统，sandbox 无关 |
