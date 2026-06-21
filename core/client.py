import asyncio
import socket
import time
from astrbot.api import logger
from pixivpy3 import ByPassSniApi, PixivError, AppPixivAPI


class PixivClientWrapper:
    """Pixiv API 客户端包装器，处理认证和定期刷新 Token"""

    def __init__(self, pixiv_config):
        self.pixiv_config = pixiv_config
        self._refresh_task: asyncio.Task | None = None
        self._auth_lock = asyncio.Lock()
        self._last_auth_success_at: float | None = None
        self._last_auth_failure_at: float | None = None
        self._auth_ttl_seconds = 30 * 60
        self._auth_failure_cooldown_seconds = 30
        self._api_call_semaphore: asyncio.Semaphore | None = None
        self._api_call_max_concurrent_requests: int | None = None

        # 根据是否配置代理选择不同的 API 客户端
        if pixiv_config.proxy:
            # 有代理时使用标准 AppPixivAPI
            self.client_api = AppPixivAPI(**pixiv_config.get_requests_kwargs())
            logger.info("Pixiv 插件：使用代理模式 (AppPixivAPI)")
        elif pixiv_config.api_proxy_host:
            # 使用 API 反代服务器
            self.client_api = AppPixivAPI()
            self.client_api.hosts = f"https://{pixiv_config.api_proxy_host}"
            logger.info(
                f"Pixiv 插件：使用 API 反代模式 ({pixiv_config.api_proxy_host})"
            )
        else:
            # 尝试多种直连方案
            self.client_api = self._create_direct_client()

    def _create_direct_client(self):
        """创建直连客户端，尝试多种方案"""
        # 方案1: 尝试标准 API 直连（部分网络环境可用）
        try:
            # 快速测试 DNS 解析
            socket.gethostbyname("oauth.secure.pixiv.net")
            logger.info("Pixiv 插件：DNS 解析成功，尝试直连模式")
            # 先测试连接
            import requests

            try:
                requests.head(
                    "https://oauth.secure.pixiv.net/", timeout=5, allow_redirects=False
                )
                logger.info("Pixiv 插件：直连测试成功，使用标准 AppPixivAPI")
                return AppPixivAPI()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                logger.info("Pixiv 插件：直连测试超时，尝试 ByPassSniApi 模式")
        except socket.gaierror:
            logger.info("Pixiv 插件：DNS 解析失败，尝试 ByPassSniApi 模式")

        # 方案2: ByPassSniApi（使用国内可用 DoH）
        client_api = ByPassSniApi()
        hosts_result = self._require_appapi_hosts_with_cn_doh(client_api)

        if hosts_result:
            logger.info(f"Pixiv 插件：使用 ByPassSniApi 模式, hosts={hosts_result}")
            return client_api

        # 方案3: 最后回退到标准直连
        logger.warning("Pixiv 插件：所有直连方案失败，回退到标准模式（可能无法连接）")
        return AppPixivAPI()

    def _require_appapi_hosts_with_cn_doh(
        self, api, hostname: str = "app-api.secure.pixiv.net", timeout: int = 10
    ) -> str | bool:
        """使用国内可用的 DoH 服务器解析 Pixiv hosts"""
        import requests

        # 优先使用国内 DoH 服务器
        doh_urls = [
            "https://doh.pub/dns-query",  # 腾讯 DoH（国内可用）
            "https://dns.alidns.com/dns-query",  # 阿里 DoH（可能可用）
            "https://1.0.0.1/dns-query",  # Cloudflare 备选
            "https://1.1.1.1/dns-query",  # Cloudflare 主
            "https://doh.dns.sb/dns-query",  # DNS.sb
        ]

        headers = {"Accept": "application/dns-json"}
        params = {
            "name": hostname,
            "type": "A",
            "do": "false",
            "cd": "false",
        }

        for url in doh_urls:
            try:
                response = requests.get(
                    url, headers=headers, params=params, timeout=timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    if "Answer" in data and data["Answer"]:
                        ip = data["Answer"][0]["data"]
                        api.hosts = f"https://{ip}"
                        return api.hosts
            except Exception:
                continue

        return False

    def _ensure_auth_state(self):
        """兼容测试或热重载期间绕过 __init__ 的包装器实例。"""
        if not hasattr(self, "_auth_lock"):
            self._auth_lock = asyncio.Lock()
        if not hasattr(self, "_last_auth_success_at"):
            self._last_auth_success_at = None
        if not hasattr(self, "_last_auth_failure_at"):
            self._last_auth_failure_at = None
        if not hasattr(self, "_auth_ttl_seconds"):
            self._auth_ttl_seconds = 30 * 60
        if not hasattr(self, "_auth_failure_cooldown_seconds"):
            self._auth_failure_cooldown_seconds = 30

    def _coerce_int_config(
        self, name: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = getattr(self.pixiv_config, name, default)
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return min(max(parsed, minimum), maximum)

    def _coerce_float_config(
        self, name: str, default: float, minimum: float, maximum: float
    ) -> float:
        value = getattr(self.pixiv_config, name, default)
        if isinstance(value, bool):
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return min(max(parsed, minimum), maximum)

    def _ensure_api_call_state(self):
        max_concurrent = self._coerce_int_config(
            "pixiv_api_max_concurrent_requests", 2, 1, 8
        )
        if (
            not hasattr(self, "_api_call_semaphore")
            or self._api_call_semaphore is None
            or getattr(self, "_api_call_max_concurrent_requests", None)
            != max_concurrent
        ):
            self._api_call_semaphore = asyncio.Semaphore(max_concurrent)
            self._api_call_max_concurrent_requests = max_concurrent

    def _is_retryable_api_error(self, error: Exception) -> bool:
        for attr in ("status_code", "status", "code"):
            value = getattr(error, attr, None)
            if value is None:
                continue
            try:
                if int(value) in {429, 500, 502, 503, 504}:
                    return True
            except (TypeError, ValueError):
                pass

        text = f"{type(error).__name__} {error}".lower()
        retryable_markers = (
            "429",
            "too many",
            "rate limit",
            "timeout",
            "temporar",
            "connection",
            "reset",
            "unavailable",
            "bad gateway",
            "gateway timeout",
        )
        if isinstance(error, (PixivError, TimeoutError, ConnectionError)):
            return any(marker in text for marker in retryable_markers)
        return any(marker in text for marker in retryable_markers)

    def _auth_cache_valid(self, now: float) -> bool:
        if self._last_auth_success_at is None:
            return False
        return now - self._last_auth_success_at < self._auth_ttl_seconds

    def _auth_failure_in_cooldown(self, now: float) -> bool:
        if self._last_auth_failure_at is None:
            return False
        return now - self._last_auth_failure_at < self._auth_failure_cooldown_seconds

    def _mark_auth_success(self):
        self._last_auth_success_at = time.monotonic()
        self._last_auth_failure_at = None

    def _mark_auth_failure(self):
        self._last_auth_failure_at = time.monotonic()

    async def authenticate(self) -> bool:
        """尝试使用配置的凭据进行 Pixiv API 认证"""
        self._ensure_auth_state()
        if not self.pixiv_config.refresh_token:
            logger.error("Pixiv 插件：未提供有效的 Refresh Token，无法进行认证。")
            return False

        now = time.monotonic()
        if self._auth_cache_valid(now):
            return True
        if self._auth_failure_in_cooldown(now):
            logger.warning("Pixiv 插件：认证失败冷却中，暂缓重复认证。")
            return False

        async with self._auth_lock:
            now = time.monotonic()
            if self._auth_cache_valid(now):
                return True
            if self._auth_failure_in_cooldown(now):
                logger.warning("Pixiv 插件：认证失败冷却中，暂缓重复认证。")
                return False

            try:
                await asyncio.to_thread(
                    self.client_api.auth, refresh_token=self.pixiv_config.refresh_token
                )
                self._mark_auth_success()
                return True
            except Exception as e:
                self._mark_auth_failure()
                logger.error(
                    f"Pixiv 插件：认证/刷新时发生错误 - 异常类型: {type(e)}, 错误信息: {e}"
                )
                return False

    async def periodic_token_refresh(self):
        """定期尝试使用 refresh_token 进行认证以保持其活性"""
        self._ensure_auth_state()
        while True:
            try:
                # 先等待指定间隔
                wait_seconds = self.pixiv_config.refresh_interval * 60
                logger.debug(
                    f"Pixiv Token 刷新任务：等待 {self.pixiv_config.refresh_interval} 分钟 ({wait_seconds} 秒)..."
                )
                await asyncio.sleep(wait_seconds)

                # 检查 refresh_token 是否已配置
                current_refresh_token = self.pixiv_config.refresh_token
                if not current_refresh_token:
                    logger.warning(
                        "Pixiv Token 刷新任务：未配置 Refresh Token，跳过本次刷新。"
                    )
                    continue

                logger.info("Pixiv Token 刷新任务：尝试使用 Refresh Token 进行认证...")
                try:
                    async with self._auth_lock:
                        await asyncio.to_thread(
                            self.client_api.auth,
                            refresh_token=current_refresh_token,
                        )
                        self._mark_auth_success()
                    logger.info("Pixiv Token 刷新任务：认证调用成功。")

                except PixivError as pe:
                    self._mark_auth_failure()
                    logger.error(
                        f"Pixiv Token 刷新任务：认证时发生 Pixiv API 错误 - {pe}"
                    )
                except Exception as e:
                    self._mark_auth_failure()
                    logger.error(
                        f"Pixiv Token 刷新任务：认证时发生未知错误 - {type(e).__name__}: {e}"
                    )
                    import traceback

                    logger.error(traceback.format_exc())

            except asyncio.CancelledError:
                logger.info("Pixiv Token 刷新任务：任务被取消，停止刷新。")
                break
            except Exception as loop_e:
                logger.error(
                    f"Pixiv Token 刷新任务：循环中发生意外错误 - {loop_e}，将在下次间隔后重试。"
                )
                import traceback

                logger.error(traceback.format_exc())

    def start_refresh_task(self) -> asyncio.Task | None:
        """启动后台刷新任务并返回任务句柄（若已启动则复用原任务）。"""
        if self.pixiv_config.refresh_interval <= 0:
            logger.info("Pixiv 插件：Refresh Token 自动刷新已禁用。")
            return None

        if self._refresh_task and not self._refresh_task.done():
            return self._refresh_task

        self._refresh_task = asyncio.create_task(self.periodic_token_refresh())
        logger.info(
            f"Pixiv 插件：已启动 Refresh Token 自动刷新任务，间隔 {self.pixiv_config.refresh_interval} 分钟。"
        )
        return self._refresh_task

    async def stop_refresh_task(self) -> None:
        """停止后台刷新任务。"""
        if not self._refresh_task or self._refresh_task.done():
            return

        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            logger.info("Pixiv Token 刷新任务已成功取消。")
        except Exception as e:
            logger.error(f"等待 Pixiv Token 刷新任务取消时发生错误: {e}")

    async def call_pixiv_api(self, func, *args, **kwargs):
        """异步调用 Pixiv API 的辅助方法"""
        self._ensure_api_call_state()
        retry_count = self._coerce_int_config("pixiv_api_retry_count", 2, 0, 5)
        base_delay = self._coerce_float_config(
            "pixiv_api_retry_base_delay", 1.0, 0.0, 60.0
        )

        async with self._api_call_semaphore:
            attempt = 0
            while True:
                try:
                    return await asyncio.to_thread(func, *args, **kwargs)
                except Exception as e:
                    if attempt >= retry_count or not self._is_retryable_api_error(e):
                        raise

                    delay = base_delay * (2**attempt)
                    attempt += 1
                    logger.warning(
                        f"Pixiv API 调用失败，将在 {delay:.2f} 秒后重试 "
                        f"{attempt}/{retry_count}: {e}"
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
