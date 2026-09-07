# -*- coding: utf-8 -*-
"""
settings 应用配置（Settings）

===========================================
什么是配置？
===========================================
配置是应用运行时的"参数"，比如：
- API 密钥
- 数据库地址
- 模型名称
- 超时时间

===========================================
为什么从环境变量读取？
===========================================
1. **安全**：密钥不写在代码里，避免泄露到 Git
2. **灵活**：不同环境（开发/测试/生产）用不同配置
3. **12-Factor App**：现代应用的最佳实践

===========================================
为什么 Infrastructure 之外不允许直接触碰 os.environ？
===========================================
- 所有配置集中在 Settings 类
- 其他模块通过 Settings 访问配置
- 避免散落在各处的 os.getenv()

===========================================
可选能力全部按"空值即关闭/降级"设计
===========================================
保证零外部依赖也能启动：
- REDIS_URL 未配 → 无缓存、无队列
- QDRANT_URL 未配 → 本地嵌入模式
- RERANKER_BASE_URL 未配 → 降级为按向量分排序
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（shopcross/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 加载 .env 文件（如果存在）
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """
    应用配置（不可变）

    所有配置项都是只读的，运行时不能修改。
    """

    # ---- 大模型配置 ----
    llm_base_url: str           # API 基础 URL
    llm_api_key: str            # API 密钥
    llm_model: str              # 模型名称（如 "qwen3-max"）
    llm_max_tokens: int         # 单次最大 token 数

    # ---- 服务配置 ----
    port: int                   # 服务端口
    log_level: str              # 日志级别

    # ---- 检索配置（模块一）----
    embedding_base_url: str     # Embedding API URL
    embedding_api_key: str      # Embedding API 密钥
    embedding_model: str        # Embedding 模型（如 "text-embedding-v4"）
    embedding_dim: int          # 向量维度（如 1024）
    qdrant_url: str             # Qdrant URL（空 = 本地模式）
    qdrant_collection: str      # Qdrant 集合名
    reranker_base_url: str      # Reranker URL（空 = 降级）
    reranker_model: str         # Reranker 模型
    tavily_api_key: str         # Tavily API 密钥（空 = 不注册 web_search_tool）

    # ---- 可观测（模块四）----
    otlp_endpoint: str          # OTLP 端点（空 = 不启用 Tracing）

    # ---- 数据目录（模块三）----
    data_dir: Path              # 数据目录（SQLite、JSON 文件落盘位置）

    # ---- 三期：品类知识库 ----
    category_kb_collection: str  # 品类知识库集合名

    # ---- 三期：Context 工程 ----
    context_size: int           # 模型上下文窗口（压缩阈值按此比例计算）
    tool_result_limit: int      # 单个工具结果字符上限
    reply_token_budget: int     # 0 = 不启用 Token 预算护栏

    # ---- 三期：工具韧性 ----
    tool_failure_threshold: int     # 连续失败达阈值后熔断
    tool_circuit_reset_seconds: float  # 熔断后多久转半开探测

    # ---- 三期：前端 ----
    cors_origins: list[str]      # CORS 允许的来源

    # ---- 四期：模型回退与网关配额闸门 ----
    # 这组给默认值：前面几期每次扩字段都会打断测试里手工构造的 Settings
    # 新增可选配置一律带默认值，避免同样的修改成本反复发生
    llm_fallback_model: str = ""        # 备用模型（空 = 不回退）
    llm_max_concurrency: int = 2        # 最大并发数
    llm_min_interval_seconds: float = 1.0  # 最小请求间隔
    llm_max_retries: int = 2            # 最大重试次数

    # ---- 四期：存储 ----
    database_url: str = ""              # 数据库 URL（空 = SQLite）

    # ---- 四期：Redis 缓存 ----
    redis_url: str = ""                 # Redis URL（空 = 无缓存）
    semantic_cache_enabled: bool = True  # 是否启用语义缓存
    semantic_cache_threshold: float = 0.95  # 相似度阈值

    # ---- 四期：队列削峰 ----
    queue_enabled: bool = True          # 是否启用队列
    queue_wait_seconds: float = 300.0   # 同步接口等待队列结果的上限
    worker_concurrency: int = 2         # worker 并发度

    # ---- 五期：运行时护栏 ----
    harness_enabled: bool = True        # 是否启用护栏
    loop_repeat_threshold: int = 3      # 循环检测阈值
    output_guard_enabled: bool = True   # 是否启用输出审核
    drift_detect_enabled: bool = False  # 是否启用漂移检测
    token_budget_total: int = 0         # 0 = 不启用 Token 预算
    breaker_shared: bool = False        # 熔断状态是否跨实例共享

    # ---- 长期记忆 ----
    preference_relevance_enabled: bool = False  # 是否启用向量相关性筛选
    preference_top_k: int = 5                   # like 注入上限
    preference_subagent_inject: bool = True     # 是否给子 Agent 注入偏好

    # ---- 队列优先级 ----
    queue_priority_enabled: bool = True         # 是否启用双队列优先级
    queue_large_request_turns: int = 30          # 长会话阈值

    # ---- Langfuse 可观测 ----
    langfuse_base_url: str = ""         # Langfuse URL（空 = 不启用）
    langfuse_public_key: str = ""       # Langfuse 公钥
    langfuse_secret_key: str = ""       # Langfuse 密钥
    langfuse_environment: str = "local"  # 环境名称

    # ---- 认证 ----
    auth_secret: str = ""               # HMAC 密钥（空 = 无认证）


def load_settings() -> Settings:
    """
    从环境变量加载配置

    返回:
        Settings 实例

    注意:
        - 同名环境变量优先于 .env 文件
        - 缺失的配置使用默认值
        - LLM_API_KEY 必须配置，否则启动失败
    """
    llm_base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm_api_key = os.getenv("LLM_API_KEY", "")
    if not llm_api_key:
        raise RuntimeError(
            "未配置 LLM_API_KEY，无法启动。请通过环境变量注入（推荐）："
            "export LLM_API_KEY=<你的密钥>；或在项目根目录创建本地 .env 文件"
            "（参考 .env.example，该文件已被 gitignore，不会入库）。"
        )
    data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=os.getenv("LLM_MODEL", "qwen3-max"),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "512")),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info"),
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL", llm_base_url),
        embedding_api_key=(
            os.getenv("SILICONFLOW_API_KEY")
            or os.getenv("EMBEDDING_API_KEY", llm_api_key)
        ),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024")),
        qdrant_url=os.getenv("QDRANT_URL", ""),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "shopcross_products"),
        reranker_base_url=os.getenv("RERANKER_BASE_URL", ""),
        reranker_model=os.getenv("RERANKER_MODEL", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        data_dir=data_dir,
        category_kb_collection=os.getenv("CATEGORY_KB_COLLECTION", "shopcross_category_kb"),
        context_size=int(os.getenv("CONTEXT_SIZE", "128000")),
        tool_result_limit=int(os.getenv("TOOL_RESULT_LIMIT", "20000")),
        reply_token_budget=int(os.getenv("REPLY_TOKEN_BUDGET", "0")),
        tool_failure_threshold=int(os.getenv("TOOL_FAILURE_THRESHOLD", "3")),
        tool_circuit_reset_seconds=float(os.getenv("TOOL_CIRCUIT_RESET_SECONDS", "60")),
        cors_origins=[
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
            if origin.strip()
        ],
        llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL", "qwen-plus"),
        llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "2")),
        llm_min_interval_seconds=float(os.getenv("LLM_MIN_INTERVAL_SECONDS", "1.0")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        database_url=(
            os.getenv("DATABASE_URL")
            or os.getenv("MYSQL_URL")
            or f"sqlite+aiosqlite:///{data_dir / 'shopcross.db'}"
        ),
        redis_url=os.getenv("REDIS_URL", ""),
        semantic_cache_enabled=os.getenv("SEMANTIC_CACHE_ENABLED", "1") not in ("0", "false", "False"),
        semantic_cache_threshold=float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95")),
        queue_enabled=os.getenv("QUEUE_ENABLED", "1") not in ("0", "false", "False"),
        queue_wait_seconds=float(os.getenv("QUEUE_WAIT_SECONDS", "300")),
        worker_concurrency=int(os.getenv("WORKER_CONCURRENCY", "2")),
        langfuse_base_url=os.getenv("LANGFUSE_BASE_URL", ""),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        langfuse_environment=os.getenv("LANGFUSE_ENVIRONMENT", "local"),
        harness_enabled=os.getenv("HARNESS_ENABLED", "1") not in ("0", "false", "False"),
        loop_repeat_threshold=int(os.getenv("LOOP_REPEAT_THRESHOLD", "3")),
        output_guard_enabled=os.getenv("OUTPUT_GUARD_ENABLED", "1") not in ("0", "false", "False"),
        drift_detect_enabled=os.getenv("DRIFT_DETECT_ENABLED", "0") not in ("0", "false", "False"),
        token_budget_total=int(os.getenv("TOKEN_BUDGET_TOTAL", "0")),
        breaker_shared=os.getenv("BREAKER_SHARED", "0") not in ("0", "false", "False"),
        preference_relevance_enabled=os.getenv("PREFERENCE_RELEVANCE_ENABLED", "0")
        not in ("0", "false", "False"),
        preference_top_k=int(os.getenv("PREFERENCE_TOP_K", "5")),
        preference_subagent_inject=os.getenv("PREFERENCE_SUBAGENT_INJECT", "1")
        not in ("0", "false", "False"),
        queue_priority_enabled=os.getenv("QUEUE_PRIORITY_ENABLED", "1") not in ("0", "false", "False"),
        queue_large_request_turns=int(os.getenv("QUEUE_LARGE_REQUEST_TURNS", "30")),
        auth_secret=os.getenv("AUTH_SECRET", ""),
    )
