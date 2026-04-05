import asyncio
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Coroutine, Optional, cast

import irc.client
import irc.connection
from astrbot import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.message_type import MessageType as CoreMessageType

from .irc_event import IRCEvent

try:
    from astrbot.core.platform.astr_message_event import MessageSesion
except Exception:
    try:
        from astrbot.core.platform.astr_message_event import MessageSession as MessageSesion
    except Exception:
        MessageSesion = Any


@register_platform_adapter(
    "irc",
    "IRC协议适配器",
    default_config_tmpl={
        "server": "irc.libera.chat",
        "port": 6667,
        "nickname": "astrbot",
        "username": "astrbot",
        "realname": "AstrBot IRC Client",
        "channels": "#test",
        "password": "",
        "ssl": False,
        "ssl_verify": True,
        "encoding": "utf-8",
        "group_wake_prefixes": "",
        "reconnect_interval": 30,
        "max_reconnect_attempts": 5,
        "max_message_length": 400,
    },
)
class IRCPlatformAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        try:
            super().__init__(config=platform_config, event_queue=event_queue)
        except TypeError:
            cast(Any, super()).__init__(event_queue)

        self.config = platform_config
        self.settings = platform_settings
        self.client = IRCClient(self)
        self.reactor = irc.client.Reactor()
        self.connection: Optional[irc.client.ServerConnection] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reactor_thread: Optional[threading.Thread] = None
        self._reactor_stop_event = threading.Event()
        self._connected_event: Optional[asyncio.Event] = None
        self._connection_error: Optional[Exception] = None
        self._stop_event = asyncio.Event()
        self._registered_channels: set[str] = set()
        self._running = False
        self._stopping = False
        self._shutdown_requested = False
        self._reconnect_attempts = 0

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(name="irc", description="IRC协议适配器", id="irc")

    async def run(self) -> None:
        logger.info("启动IRC适配器，连接到 %s:%s", self.config["server"], self.config["port"])

        self._loop = asyncio.get_running_loop()
        self._running = True
        self._stopping = False
        self._shutdown_requested = False
        self._stop_event.clear()
        self._connection_error = None
        self._bind_client_callbacks()

        await self._connect_forever()

    async def stop(self):
        logger.info("停止IRC适配器")
        self._shutdown_requested = True
        self._stopping = True
        self._running = False
        self._stop_event.set()
        self._connection_error = None

        if self._connected_event is not None and not self._connected_event.is_set():
            self._connection_error = ConnectionError("IRC适配器已停止")
            self._connected_event.set()

        await self._disconnect(expect_shutdown=True)

        if self._reactor_thread and self._reactor_thread.is_alive():
            self._reactor_thread.join(timeout=5)

        self._reactor_thread = None
        self.connection = None

    async def send_by_session(self, session: Any, message_chain: MessageChain):
        target = self._extract_session_target(session)
        message = self._message_chain_to_text(message_chain)

        logger.info("IRC平台发送: session=%r target=%r message=%r", session, target, message)

        if not target:
            logger.warning("IRC平台发送失败: 无法解析 target, session=%r", session)
            return
        if not message:
            logger.warning("IRC平台发送失败: 消息内容为空, session=%r chain=%r", session, message_chain)
            return

        self.send_message(target, message)

        try:
            await super().send_by_session(session, message_chain)
        except Exception as exc:
            logger.debug("IRC super().send_by_session 执行失败: %s", exc)

    async def _connect_forever(self):
        while self._running and not self._stopping:
            try:
                self._stop_event.clear()
                await self._connect_once()
                await self._stop_event.wait()
                if self._stopping or self._shutdown_requested:
                    return

                raise self._connection_error or ConnectionError("连接断开")
            except Exception as exc:
                logger.error("IRC连接错误: %s", exc)

                if self._stopping or self._shutdown_requested:
                    logger.info("IRC适配器正在停止，跳过重连")
                    return

                self._reconnect_attempts += 1
                max_attempts = int(self.config.get("max_reconnect_attempts", 5) or 5)
                if self._reconnect_attempts > max_attempts:
                    logger.error("达到最大重连次数 %s，停止重连", max_attempts)
                    self._running = False
                    return

                interval = int(self.config.get("reconnect_interval", 30) or 30)
                logger.info("将在 %s 秒后尝试重连 (尝试 %s/%s)", interval, self._reconnect_attempts, max_attempts)
                await asyncio.sleep(interval)

    async def _connect_once(self):
        self._connected_event = asyncio.Event()
        self._connection_error = None
        self._reactor_stop_event.clear()
        self.reactor = irc.client.Reactor()
        self.connection = None

        logger.info(
            "开始建立IRC连接: server=%s port=%s ssl=%s nickname=%s username=%s",
            self.config.get("server"),
            self.config.get("port"),
            self._get_bool_config("ssl", False),
            self.config.get("nickname"),
            self.config.get("username", self.config.get("nickname")),
        )

        factory = self._create_connection_factory()
        self.connection = self.reactor.server().connect(
            self.config["server"],
            self.config["port"],
            self.config["nickname"],
            password=self.config.get("password"),
            username=self.config.get("username", self.config["nickname"]),
            ircname=self.config.get("realname", "AstrBot IRC Client"),
            connect_factory=factory,
        )
        self._register_handlers()

        self._reactor_thread = threading.Thread(
            target=self._run_reactor,
            daemon=True,
            name=f"IRC-Reactor-{self.config['server']}",
        )
        self._reactor_thread.start()

        await asyncio.wait_for(self._connected_event.wait(), timeout=15)
        if self._connection_error is not None:
            raise self._connection_error

        self._reconnect_attempts = 0
        await self._join_channels()
        logger.info("IRC适配器连接成功")

    async def _disconnect(self, expect_shutdown: bool):
        connection = self.connection
        self.connection = None
        self._registered_channels.clear()
        self._reactor_stop_event.set()

        if not connection:
            return

        try:
            if connection.connected:
                if expect_shutdown:
                    for channel in self._get_config_channels():
                        try:
                            logger.info("离开频道: %s", channel)
                            connection.part(channel, "AstrBot shutting down")
                            await asyncio.sleep(0.15)
                        except Exception as exc:
                            logger.debug("离开频道失败 %s: %s", channel, exc)

                    try:
                        connection.quit("AstrBot shutting down")
                    except Exception:
                        pass
                    self._connection_error = None
                else:
                    try:
                        self._connection_error = ConnectionError("连接断开")
                        connection.disconnect("IRC disconnected")
                    except Exception:
                        pass
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _bind_client_callbacks(self):
        self.client.on_privmsg = self._on_privmsg
        self.client.on_pubmsg = self._on_pubmsg
        self.client.on_join = self._on_join
        self.client.on_part = self._on_part
        self.client.on_welcome = self._on_welcome
        self.client.on_disconnect = self._on_disconnect
        self.client.on_nicknameinuse = self._on_nicknameinuse
        self.client.on_error = self._on_error

    def _create_connection_factory(self):
        if self._get_bool_config("ssl", False):
            import ssl
            from functools import partial

            ssl_context = ssl.create_default_context()
            server_hostname = str(self.config.get("server", "") or "").strip() or None
            if not self._get_bool_config("ssl_verify", True):
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            wrap_socket = partial(ssl_context.wrap_socket, server_hostname=server_hostname)
            return irc.connection.Factory(wrapper=wrap_socket)

        return irc.connection.Factory()

    def _register_handlers(self):
        if not self.connection:
            return

        self.connection.add_global_handler("welcome", self.client.handle_welcome)
        self.connection.add_global_handler("privmsg", self.client.handle_privmsg)
        self.connection.add_global_handler("pubmsg", self.client.handle_pubmsg)
        self.connection.add_global_handler("join", self.client.handle_join)
        self.connection.add_global_handler("part", self.client.handle_part)
        self.connection.add_global_handler("disconnect", self.client.handle_disconnect)
        self.connection.add_global_handler("nicknameinuse", self.client.handle_nicknameinuse)
        self.connection.add_global_handler("error", self.client.handle_error)

    def _run_reactor(self):
        try:
            while not self._reactor_stop_event.is_set():
                self.reactor.process_once(timeout=1)
        except Exception as exc:
            logger.error("IRC reactor错误: %s", exc)
            self._submit_coro(self._handle_reactor_error(exc))

    def _submit_coro(self, coro: Coroutine[Any, Any, Any]) -> Optional[Future]:
        if self._loop is None or self._loop.is_closed():
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _handle_reactor_error(self, error: Exception):
        if self._stopping or self._shutdown_requested:
            return
        self._connection_error = error
        if self._connected_event is not None and not self._connected_event.is_set():
            self._connected_event.set()

    async def _join_channels(self):
        if not self.connection:
            return

        for channel in self._get_config_channels():
            logger.info("加入频道: %s", channel)
            self._registered_channels.add(channel)
            self.connection.join(channel)
            await asyncio.sleep(0.5)

    def _get_config_channels(self) -> list[str]:
        raw_channels = str(self.config.get("channels", "") or "")
        channels: list[str] = []
        for item in raw_channels.split(","):
            channel = item.strip()
            if not channel:
                continue
            if not channel.startswith("#"):
                channel = f"#{channel}"
            channels.append(channel)
        return channels

    def get_group_wake_prefixes(self) -> list[str]:
        configured = self.config.get("group_wake_prefixes")
        if isinstance(configured, str) and configured.strip():
            return [item.strip() for item in configured.split(",") if item.strip()]
        if isinstance(configured, list):
            return [str(item).strip() for item in configured if str(item).strip()]

        nickname = str(self.config.get("nickname", "") or "").strip()
        if not nickname:
            return []
        return [f"{nickname}:", f"{nickname}：", f"@{nickname}", f"{nickname},", f"{nickname}，", f"{nickname} "]

    def _extract_group_wake_message(self, raw_message: str) -> str:
        text = str(raw_message or "").strip()
        if not text:
            return ""

        for prefix in self.get_group_wake_prefixes():
            if text.lower().startswith(prefix.lower()):
                return text[len(prefix):].strip()
        return ""

    async def _on_privmsg(self, connection, event):
        source = event.source
        message = event.arguments[0] if event.arguments else ""
        if source.nick == self.config["nickname"]:
            return
        await self._handle_message(event, MessageType.FRIEND_MESSAGE, message)

    async def _on_pubmsg(self, connection, event):
        source = event.source
        message = event.arguments[0] if event.arguments else ""
        if source.nick == self.config["nickname"]:
            return

        wake_message = self._extract_group_wake_message(message)
        if not wake_message:
            logger.debug("忽略未唤醒的频道消息: %s -> %s: %s", source.nick, event.target, message[:50])
            return

        await self._handle_message(event, MessageType.GROUP_MESSAGE, wake_message)

    async def _on_join(self, connection, event):
        logger.info("加入频道: %s 加入了 %s", event.source.nick, event.target)

    async def _on_part(self, connection, event):
        logger.info("离开频道: %s 离开了 %s", event.source.nick, event.target)

    async def _on_welcome(self, connection, event):
        logger.info("IRC连接成功: %s", event.arguments[0] if event.arguments else "Welcome")
        if self._connected_event is not None and not self._connected_event.is_set():
            self._connected_event.set()

    async def _on_disconnect(self, connection, event):
        logger.info("IRC连接断开")

        if self._shutdown_requested or self._stopping:
            self._connection_error = None
            return

        self._connection_error = ConnectionError("连接断开")
        if self._connected_event is not None and not self._connected_event.is_set():
            self._connected_event.set()

        self._stop_event.set()
        self._submit_coro(self._disconnect(expect_shutdown=False))

    async def _on_nicknameinuse(self, connection, event):
        logger.warning("昵称 %s 已被使用", self.config["nickname"])
        new_nick = self.config["nickname"] + "_"
        self.config["nickname"] = new_nick
        connection.nick(new_nick)
        logger.info("尝试使用新昵称: %s", new_nick)

    async def _on_error(self, connection, event):
        error_msg = event.arguments[0] if event.arguments else "未知错误"

        if self._shutdown_requested or self._stopping:
            logger.info("IRC停机阶段收到错误事件，忽略: %s", error_msg)
            return

        logger.error("IRC错误: %s", error_msg)

        lowered = str(error_msg).lower()
        if any(keyword in lowered for keyword in ("closing link", "connection reset", "ping timeout", "broken pipe")):
            self._connection_error = ConnectionError(error_msg)
            if self._connected_event is not None and not self._connected_event.is_set():
                self._connected_event.set()
            self._stop_event.set()

    async def _handle_message(self, event, message_type: MessageType, message_text: str):
        try:
            source = event.source
            target = event.target
            is_group = self._is_group_message_type(message_type)
            target_id = target if is_group else source.nick
            session_id = self._build_session_id(target_id, source.nick, is_group)

            message = self._create_astr_message()
            if message is None:
                return

            sender = self._build_message_member(source.nick, source.nick)
            self._safe_set_message_field(message, "type", message_type)
            self._safe_set_message_field(message, "message_type", message_type)
            self._safe_set_message_field(message, "session_id", session_id)
            self._safe_set_message_field(message, "group_id", target if is_group else "")
            self._safe_set_message_field(message, "conversation_id", target_id)
            self._safe_set_message_field(message, "platform", "irc")
            self._safe_set_message_field(message, "platform_name", "irc")
            self._safe_set_message_field(message, "platform_id", "irc")
            self._safe_set_message_field(message, "is_group", is_group)
            self._safe_set_message_field(message, "message_str", message_text)
            self._safe_set_message_field(message, "message", [Plain(text=message_text)])
            self._safe_set_message_field(message, "sender", sender)
            self._safe_set_message_field(message, "member", sender)
            self._safe_set_message_field(message, "self_id", self.config["nickname"])
            self._safe_set_message_field(message, "user_id", source.nick)
            self._safe_set_message_field(message, "sender_id", source.nick)
            self._safe_set_message_field(message, "target_id", target_id)
            self._safe_set_message_field(message, "session", self._build_session_payload(session_id, target_id, source.nick, is_group, message))
            self._safe_set_message_field(message, "message_session", self._build_session_payload(session_id, target_id, source.nick, is_group, message))
            self._safe_set_message_field(message, "raw_message", {
                "event": event.type,
                "source": source.nick,
                "target": target,
                "message": message_text,
                "arguments": event.arguments,
            })
            self._safe_set_message_field(message, "message_id", f"irc_{int(time.time())}_{abs(hash(str(event))) % 10000}")

            logger.info(
                "提交IRC事件: type=%r session_id=%r group_id=%r user_id=%r message=%r",
                getattr(message, "type", None),
                session_id,
                getattr(message, "group_id", None),
                getattr(message, "user_id", None),
                message_text,
            )

            self.commit_event(
                IRCEvent(
                    message_str=message_text,
                    message_obj=message,
                    platform_meta=self.meta(),
                    session_id=session_id,
                    client=self.client,
                    connection=self.connection,
                    session=self._build_session_payload(session_id, target_id, source.nick, is_group, message),
                )
            )
        except Exception as exc:
            logger.error("提交IRC事件失败: %s", exc)

    def _build_session_payload(
        self,
        session_id: str,
        target_id: str,
        user_id: str,
        is_group: bool,
        message_obj: AstrBotMessage,
    ) -> Any:
        payload = {
            "session_id": session_id,
            "sid": session_id,
            "id": session_id,
            "target": target_id,
            "target_id": target_id,
            "group_id": target_id if is_group else "",
            "user_id": user_id,
            "channel": target_id if is_group else "",
            "scope": "group" if is_group else "private",
            "platform": "irc",
            "platform_name": "irc",
            "message_obj": message_obj,
        }

        core_message_type = CoreMessageType.GROUP_MESSAGE if is_group else CoreMessageType.FRIEND_MESSAGE

        try:
            session_cls = cast(Any, MessageSesion)
            session = session_cls(
                session_id=session_id,
                platform_name="irc",
                message_type=core_message_type,
            )
            for key, value in payload.items():
                try:
                    setattr(session, key, value)
                except Exception:
                    pass
            return session
        except Exception:
            return type("IRCSession", (), payload)()

    def send_message(self, target: str, message: str):
        if not self.connection or not self.connection.connected:
            raise ConnectionError("IRC未连接")

        logger.info("IRC原生发送: target=%r message=%r", target, message)
        limit = int(self.config.get("max_message_length", 400) or 400)
        chunks = [message[i:i + limit] for i in range(0, len(message), limit)] or [message]
        for chunk in chunks:
            logger.debug("IRC分片发送: target=%r chunk=%r", target, chunk)
            self.connection.privmsg(target, chunk)

    def _message_chain_to_text(self, message_chain: MessageChain) -> str:
        if message_chain is None:
            return ""
        if isinstance(message_chain, str):
            return message_chain.strip()

        for attr_name in ("text", "content", "message", "delta"):
            value = getattr(message_chain, attr_name, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and value is not message_chain:
                nested = self._message_chain_to_text(cast(Any, value))
                if nested:
                    return nested

        components = getattr(message_chain, "chain", None)
        if components is None:
            components = getattr(message_chain, "messages", None)
        if components is None and isinstance(message_chain, (list, tuple)):
            components = message_chain

        parts: list[str] = []
        for component in components or []:
            text = getattr(component, "text", None)
            if text:
                parts.append(str(text))
                continue

            target = getattr(component, "qq", None) or getattr(component, "target", None)
            if target:
                parts.append(f"@{target}")
                continue

            file_value = getattr(component, "file", None) or getattr(component, "url", None) or getattr(component, "path", None)
            if file_value:
                parts.append(f"[资源: {file_value}]")
                continue

            parts.append(str(component))

        return "".join(parts).strip()

    def _extract_session_target(self, session: Any) -> str:
        if session is None:
            return ""

        for attr in ("target", "group_id", "user_id", "channel"):
            value = getattr(session, attr, None)
            if value:
                return str(value)

        message_obj = getattr(session, "message_obj", None)
        if message_obj is not None:
            target = getattr(message_obj, "target_id", None) or getattr(message_obj, "group_id", None) or getattr(message_obj, "user_id", None)
            if target:
                return str(target)

        session_id = getattr(session, "session_id", None) or getattr(session, "sid", None) or getattr(session, "id", None) or ""
        parsed = self._parse_session_id(session_id)
        return parsed["target"]

    def _parse_session_id(self, session_id: Any) -> dict[str, str]:
        value = str(session_id or "").strip()
        parts = value.split(":", 3)
        if len(parts) == 4 and parts[0] == "irc":
            return {"platform": parts[0], "scope": parts[1], "target": parts[2], "user_id": parts[3]}
        return {"platform": "", "scope": "", "target": value, "user_id": ""}

    def _build_session_id(self, target: str, user_id: str, is_group: bool) -> str:
        scope = "group" if is_group else "private"
        return f"irc:{scope}:{str(target or '').strip()}:{str(user_id or '').strip()}"

    def _is_group_message_type(self, message_type: Any) -> bool:
        try:
            return message_type == MessageType.GROUP_MESSAGE or getattr(message_type, "name", "") == "GROUP_MESSAGE"
        except Exception:
            return False

    def _create_astr_message(self) -> Optional[AstrBotMessage]:
        try:
            return AstrBotMessage()
        except Exception:
            logger.error("创建AstrBotMessage失败")
            return None

    def _build_message_member(self, user_id: str, nickname: str) -> Any:
        try:
            return MessageMember(user_id=user_id, nickname=nickname)
        except Exception:
            return {"user_id": user_id, "nickname": nickname, "id": user_id, "name": nickname}

    def _safe_set_message_field(self, message: AstrBotMessage, field_name: str, value: Any) -> None:
        try:
            setattr(message, field_name, value)
        except Exception:
            pass

    def _get_bool_config(self, key: str, default: bool = False) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)


class IRCClient:
    def __init__(self, adapter: IRCPlatformAdapter):
        self.adapter = adapter
        self.on_privmsg: Optional[Callable] = None
        self.on_pubmsg: Optional[Callable] = None
        self.on_join: Optional[Callable] = None
        self.on_part: Optional[Callable] = None
        self.on_welcome: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.on_nicknameinuse: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

    def handle_privmsg(self, connection, event):
        if self.on_privmsg:
            self.adapter._submit_coro(self.on_privmsg(connection, event))

    def handle_pubmsg(self, connection, event):
        if self.on_pubmsg:
            self.adapter._submit_coro(self.on_pubmsg(connection, event))

    def handle_join(self, connection, event):
        if self.on_join:
            self.adapter._submit_coro(self.on_join(connection, event))

    def handle_part(self, connection, event):
        if self.on_part:
            self.adapter._submit_coro(self.on_part(connection, event))

    def handle_welcome(self, connection, event):
        if self.on_welcome:
            self.adapter._submit_coro(self.on_welcome(connection, event))

    def handle_disconnect(self, connection, event):
        if self.on_disconnect:
            self.adapter._submit_coro(self.on_disconnect(connection, event))

    def handle_nicknameinuse(self, connection, event):
        if self.on_nicknameinuse:
            self.adapter._submit_coro(self.on_nicknameinuse(connection, event))

    def handle_error(self, connection, event):
        if self.on_error:
            self.adapter._submit_coro(self.on_error(connection, event))
