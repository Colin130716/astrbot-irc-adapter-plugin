from typing import Any, List, Optional, cast

import irc.client
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, Face, Image, Plain
from astrbot.api.platform import AstrBotMessage, PlatformMetadata


class IRCEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: Any,
        connection: Optional[irc.client.ServerConnection] = None,
        session: Any = None,
    ):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        self.connection = connection
        self.session = session
        self.message_session = session

    async def send(self, message: MessageChain):
        target = self._get_target()
        if not target or not self.connection or not self.connection.connected:
            logger.warning(
                "IRCEvent.send 跳过: target=%r connected=%r chain=%r",
                target,
                bool(self.connection and self.connection.connected),
                message,
            )
            return

        text = self._build_message_text(message)
        logger.info("IRCEvent.send target=%r text=%r", target, text)
        if not text:
            logger.warning("IRCEvent.send 未提取到文本: %r", message)
            return

        for chunk in self._split_irc_messages(text):
            self.connection.privmsg(target, chunk)


    async def reply(self, message: str):
        target = self._get_target()
        if not target or not self.connection or not self.connection.connected:
            logger.warning(
                "IRCEvent.reply 跳过: target=%r connected=%r text=%r",
                target,
                bool(self.connection and self.connection.connected),
                message,
            )
            return

        logger.info("IRCEvent.reply target=%r text=%r", target, message)
        for chunk in self._split_irc_messages(message):
            self.connection.privmsg(target, chunk)


    def _get_target(self) -> Optional[str]:
        if not self.message_obj:
            return None

        group_id = getattr(self.message_obj, "group_id", None)
        if group_id:
            return str(group_id)

        sender = getattr(self.message_obj, "sender", None) or getattr(self.message_obj, "member", None)
        sender_id = getattr(sender, "user_id", None) or getattr(self.message_obj, "sender_id", None) or getattr(self.message_obj, "user_id", None)
        if sender_id:
            return str(sender_id)

        session_id = getattr(self.message_obj, "session_id", None) or getattr(self, "session_id", None)
        if session_id:
            parts = str(session_id).split(":", 3)
            if len(parts) == 4:
                return parts[2]
            return str(session_id)
        return None

    def _build_message_text(self, message: MessageChain) -> str:
        if message is None:
            return ""
        if isinstance(message, str):
            return self._normalize_irc_text(message)

        component_type = getattr(message, "type", None)
        if component_type is not None:
            type_name = getattr(component_type, "value", None) or getattr(component_type, "name", None) or str(component_type)
            if str(type_name).lower() == "reply":
                chained = getattr(message, "chain", None) or getattr(message, "message", None) or getattr(message, "messages", None)
                nested = self._build_message_text(cast(Any, chained))
                if nested:
                    return nested

                for attr_name in ("message_str", "text", "content"):
                    value = getattr(message, attr_name, None)
                    if isinstance(value, str) and value.strip():
                        return self._normalize_irc_text(value)

        for attr_name in ("text", "content", "message", "delta"):
            value = getattr(message, attr_name, None)
            if isinstance(value, str) and value.strip():
                return self._normalize_irc_text(value)
            if value is not None and value is not message:
                nested = self._build_message_text(cast(Any, value))
                if nested:
                    return nested

        parts: List[str] = []
        for component in self._iter_message_components(message):
            if isinstance(component, Plain):
                parts.append(component.text)
            elif isinstance(component, At):
                target = getattr(component, "qq", None) or getattr(component, "target", None) or ""
                parts.append(f"@{target} ")
            elif isinstance(component, Face):
                face_id = getattr(component, "id", None) or getattr(component, "face_id", None) or ""
                parts.append(f"[表情{face_id}]")
            elif isinstance(component, Image):
                file_value = getattr(component, "file", None) or getattr(component, "url", None) or getattr(component, "path", None)
                if file_value and str(file_value).startswith("http"):
                    parts.append(f"[图片: {file_value}]")
                else:
                    parts.append("[图片]")
            else:
                component_type = getattr(component, "type", None)
                if component_type is not None:
                    type_name = getattr(component_type, "value", None) or getattr(component_type, "name", None) or str(component_type)
                    if str(type_name).lower() == "reply":
                        nested = self._build_message_text(cast(Any, component))
                        if nested:
                            parts.append(nested)
                            continue

                text = getattr(component, "text", None)
                if text:
                    parts.append(str(text))
                else:
                    parts.append(str(component))

        return self._normalize_irc_text("".join(parts).strip())

    def _iter_message_components(self, message: MessageChain):
        components = getattr(message, "chain", None)
        if components is None:
            components = getattr(message, "messages", None)
        if components is None and isinstance(message, (list, tuple)):
            components = message
        return components or []

    def _normalize_irc_text(self, text: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in normalized.split("\n")]
        return "\n".join(line for line in lines if line)

    def _split_irc_messages(self, text: str, limit: int = 400) -> List[str]:
        normalized = self._normalize_irc_text(text)
        if not normalized:
            return []

        messages: List[str] = []
        for line in normalized.split("\n"):
            if not line:
                continue
            for index in range(0, len(line), limit):
                chunk = line[index:index + limit].strip()
                if chunk:
                    messages.append(chunk)
        return messages
