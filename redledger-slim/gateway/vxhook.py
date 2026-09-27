from __future__ import annotations

import codecs
import re
from datetime import datetime
from typing import Any


TEXT_TYPES = {"1", "text", "txt"}
IMAGE_TYPES = {"3", "image", "img", "picture"}
VOICE_TYPES = {"34", "voice", "audio"}
VIDEO_TYPES = {"43", "video"}
EMOJI_TYPES = {"47", "emoji", "emoticon"}
FILE_TYPES = {"49", "file", "link", "appmsg"}
REVOKE_XML_ID_RE = re.compile(r"<(msgid|newmsgid)>\s*([^<]+?)\s*</\1>", re.IGNORECASE)


def normalize_vxhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize common VXHook callback shapes into the ledger message contract.

    Real-world WeChat hooks vary heavily. This adapter keeps the first run useful:
    it accepts nested JSON, common English/Chinese-ish field names, and the usual
    group-message pattern where content is prefixed as "member_wxid:\\nmessage".
    """

    flat = _flatten(payload)
    event_type = _string(_pick(flat, "event_type", "eventType", "EventType"))
    event_desc = _string(_pick(flat, "event_desc", "eventDesc", "EventDesc", "desc", "description"))
    from_user = _string(_pick(flat, "fromUserName", "from_user_name", "fromUser", "from_user"))
    to_user = _string(_pick(flat, "toUserName", "to_user_name", "toUser", "to_user"))
    account_wxid = _string(_pick(flat, "account_wxid", "accountWxid", "selfWxid", "self_wxid"))
    revoke_message_id = _string(
        _pick(
            flat,
            "revoke_message_id",
            "revokeMessageId",
            "revoke_msgid",
            "revoke_msgId",
            "revoke_msg_id",
            "content_xml.sysmsg.revokemsg.msgid",
            "sysmsg.revokemsg.msgid",
            "revokemsg.msgid",
        )
    )
    revoke_new_message_id = _string(
        _pick(
            flat,
            "revoke_new_message_id",
            "revoke_newMsgId",
            "revoke_newmsgid",
            "content_xml.sysmsg.revokemsg.newmsgid",
            "sysmsg.revokemsg.newmsgid",
            "revokemsg.newmsgid",
        )
    )
    revoke_session = _string(
        _pick(
            flat,
            "revoke_session",
            "content_xml.sysmsg.revokemsg.session",
            "sysmsg.revokemsg.session",
            "revokemsg.session",
            "revoke_toUserName",
            "revoke_fromUserName",
        )
    )

    raw_content = _pick(
        flat,
        "real_content",
        "realContent",
        "content",
        "msg",
        "message",
        "text",
        "msgContent",
        "msg_content",
        "messageContent",
        "contentText",
        "xml",
    )
    content = _string(raw_content)

    sender_name = _string(
        _pick(
            flat,
            "sender_name",
            "senderName",
            "sender",
            "member_info.nickName",
            "member_info.nickName.String",
            "member_info.remark",
            "member_info.remark.String",
            "member_info.userName",
            "member_info.userName.String",
            "sender_profile.nickName",
            "sender_profile.nickName.String",
            "sender_profile.remark",
            "sender_profile.remark.String",
            "sender_profile.userName",
            "sender_profile.userName.String",
            "fromNickName",
            "fromNickname",
            "nickName",
            "nickname",
            "actualNickName",
            "memberName",
            "remark",
        )
    )
    sender_wxid = _string(
        _pick(
            flat,
            "wxid",
            "sender_wxid",
            "senderWxid",
            "room_sender_by",
            "roomSenderBy",
            "member_info.userName",
            "member_info.userName.String",
            "sender_profile.userName",
            "sender_profile.userName.String",
            "sender_profile.friendUserName",
            "sender_profile.friendUserName.String",
            "fromWxid",
            "fromUserName",
            "from_user_name",
            "actualUserName",
            "memberWxid",
            "member_wxid",
        )
    )
    wechat_account = _string(
        _pick(
            flat,
            "wechat_account", "wechatAccount", "wechat_id", "wechatId",
        )
    )
    if sender_wxid.endswith("@chatroom"):
        sender_wxid = ""
    talker = _string(_pick(flat, "talker", "talkerId", "fromUser", "from_user", "roomid", "room_id", "chatroom"))
    group_id = _string(
        _pick(flat, "group_id", "groupWxid", "group_wxid", "chatroomId", "chatroom_id", "roomid", "room_id")
    )
    group_name = _string(_pick(flat, "group_name", "groupName", "chatroomName", "roomName"))
    if not group_name:
        if from_user.endswith("@chatroom"):
            group_name = _string(
                _pick(
                    flat,
                    "sender_profile.nickName",
                    "sender_profile.nickName.String",
                    "sender_profile.remark",
                    "sender_profile.remark.String",
                )
            )
        elif to_user.endswith("@chatroom"):
            group_name = _string(
                _pick(
                    flat,
                    "touser_profile.nickName",
                    "touser_profile.nickName.String",
                    "touser_profile.remark",
                    "touser_profile.remark.String",
                )
            )
    group_avatar_url = _string(
        _pick(
            flat,
            "group_avatar_url",
            "groupAvatarUrl",
            "groupAvatar",
            "chatroom_avatar_url",
            "chatroomAvatarUrl",
            "room_avatar_url",
            "roomAvatarUrl",
            "groupHeadImgUrl",
        )
    )

    for candidate in (group_id, talker, from_user, to_user, revoke_session):
        if candidate.endswith("@chatroom"):
            group_id = candidate
            break
    if not group_id and not sender_wxid and talker:
        sender_wxid = talker
    if group_id and not sender_wxid and account_wxid and to_user == group_id:
        sender_wxid = account_wxid

    prefix_sender, stripped_content = _split_group_sender(content)
    if prefix_sender:
        content = stripped_content
        if not sender_wxid and _looks_like_wxid(prefix_sender):
            sender_wxid = prefix_sender
        if not sender_name:
            sender_name = prefix_sender

    if not sender_name:
        sender_name = sender_wxid or group_id or "未知成员"

    raw_msg_type = _pick(flat, "message_type", "msgType", "msg_type", "type", "localType")
    msg_type = normalize_message_type(raw_msg_type)
    if _looks_like_revoke(event_type, event_desc, raw_msg_type, content, revoke_message_id):
        msg_type = "revoke"
        if not sender_wxid and sender_name in {"", group_id, "未知成员"}:
            revoke_sender = _revoke_sender_name(
                _string(
                    _pick(
                        flat,
                        "content_xml.sysmsg.revokemsg.replacemsg",
                        "sysmsg.revokemsg.replacemsg",
                        "revokemsg.replacemsg",
                        "replacemsg",
                    )
                )
            )
            if revoke_sender:
                sender_name = revoke_sender
    if _looks_like_red_packet(content):
        msg_type = "red_packet"
    if msg_type == "emoji" and content.strip() in {"1", "2", "3", "4"}:
        msg_type = "emoji_result"
    if msg_type in {"image", "emoji", "file", "text"}:
        content = _augment_image_content(content, flat)

    received_at = normalize_time(_pick(flat, "received_at", "createTime", "create_time", "msgTime", "time", "timestamp"))
    transport_message_id = _string(_pick(flat, "msgId", "msgid", "MsgId"))
    server_message_id = _string(_pick(flat, "newMsgId", "new_msg_id", "NewMsgId"))
    client_message_id = _string(_pick(flat, "clientMsgId", "client_msg_id"))
    local_message_id = _string(_pick(flat, "localId", "local_id"))
    message_id = _string(
        _pick(
            flat,
            "message_id",
            "messageId",
            "msgId",
            "msgid",
            "MsgId",
            "clientMsgId",
            "client_msg_id",
            "newMsgId",
            "localId",
            "local_id",
        )
    )
    message_ids = _unique_strings(
        message_id,
        _pick(flat, "msgId", "msgid", "MsgId"),
        _pick(flat, "newMsgId", "new_msg_id", "newmsgid", "NewMsgId"),
        _pick(flat, "clientMsgId", "client_msg_id"),
        _pick(flat, "localId", "local_id"),
    )
    revoke_message_ids = _unique_strings(
        revoke_message_id,
        revoke_new_message_id,
        _pick(flat, "revoke_msgId", "revoke_msgid", "revoke_msg_id"),
        _pick(flat, "revoke_newMsgId", "revoke_newmsgid", "revoke_new_message_id"),
        _extract_revoke_xml_ids(content),
    )

    return {
        "session_id": _pick(flat, "session_id", "sessionId"),
        "hook_event_type": event_type,
        "hook_event_desc": event_desc,
        "message_id": message_id,
        "message_ids": message_ids,
        "transport_message_id": transport_message_id,
        "server_message_id": server_message_id,
        "client_message_id": client_message_id,
        "local_message_id": local_message_id,
        "sender_name": sender_name,
        "wxid": sender_wxid,
        "wechat_account": wechat_account,
        "group_id": group_id,
        "group_name": group_name,
        "group_avatar_url": group_avatar_url,
        "avatar_url": _string(
            _pick(
                flat,
                "avatar_url",
                "avatar",
                "member_info.smallHeadImgUrl",
                "member_info.bigHeadImgUrl",
                "sender_profile.smallHeadImgUrl",
                "sender_profile.bigHeadImgUrl",
                "headImg",
                "headimg",
                "headImgUrl",
            )
        ),
        "message_type": msg_type,
        "content": content.strip(),
        "revoke_message_id": revoke_message_id,
        "revoke_new_message_id": revoke_new_message_id,
        "revoke_message_ids": revoke_message_ids,
        "revoke_session": revoke_session,
        "is_banker": _bool(_pick(flat, "is_banker", "isBanker", "banker")),
        "is_group_admin": _bool(
            _pick(
                flat,
                "is_group_admin",
                "isGroupAdmin",
                "group_admin",
                "groupAdmin",
                "is_admin",
                "isAdmin",
                "admin",
                "is_manager",
                "isManager",
                "manager",
                "member_info.is_group_admin",
                "member_info.isGroupAdmin",
                "member_info.group_admin",
                "member_info.groupAdmin",
                "member_info.is_admin",
                "member_info.isAdmin",
                "member_info.admin",
                "member_info.is_manager",
                "member_info.isManager",
                "member_info.manager",
            )
        ),
        "is_self": bool((account_wxid and sender_wxid == account_wxid) or "自己发送" in event_desc),
        "received_at": received_at,
    }


def normalize_message_type(value: Any) -> str:
    text = _string(value).strip().lower()
    if text in TEXT_TYPES or not text:
        return "text"
    if text in IMAGE_TYPES:
        return "image"
    if text in VOICE_TYPES:
        return "voice"
    if text in VIDEO_TYPES:
        return "video"
    if text in EMOJI_TYPES:
        return "emoji"
    if text in FILE_TYPES:
        return "file"
    if text in {"revoke", "recall", "withdraw"}:
        return "revoke"
    return text


def _looks_like_revoke(event_type: str, event_desc: str, raw_msg_type: Any, content: str, revoke_message_id: str) -> bool:
    raw = _string(raw_msg_type).strip().lower()
    text = f"{event_type} {event_desc} {content}".lower()
    if '<sysmsg type="pat"' in text and not revoke_message_id and "revokemsg" not in text and "revoke" not in text and "撤回" not in text:
        return False
    return bool(
        revoke_message_id
        or raw in {"revoke", "recall", "withdraw"}
        or (raw == "10002" and ("revokemsg" in text or "撤回" in text or "revoke" in text))
        or "撤回" in text
        or "revokemsg" in text
        or "revoke" in text
    )


def _revoke_sender_name(replace_message: str) -> str:
    text = replace_message.strip()
    if not text:
        return ""
    match = re.match(r'^["“]?(.+?)["”]?\s*撤回了一条消息', text)
    if not match:
        return ""
    return match.group(1).strip().strip('"“”')


def _looks_like_red_packet(content: str) -> bool:
    text = content.lower()
    if not text:
        return False
    return (
        "微信红包" in content
        or "hongbao" in text
        or "wcpayinfo" in text
        or "mmpayhb" in text
        or "wxhb_personalreceive" in text
        or "<type><![cdata[2001]]></type>" in text
        or "<type>2001</type>" in text
        or "sendid=" in text
    )


def normalize_time(value: Any) -> str | None:
    raw = _string(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        timestamp = int(raw)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    return raw


def _augment_image_content(content: str, flat: dict[str, Any]) -> str:
    parts = [content.strip()]
    for key in (
        "md5",
        "originsourcemd5",
        "originSourceMd5",
        "filemd5",
        "fileMd5",
        "thumbmd5",
        "thumbMd5",
        "androidmd5",
        "androidMd5",
        "externmd5",
        "externMd5",
        "content_xml.msg.img.md5",
        "content_xml.msg.img.originsourcemd5",
        "content_xml.msg.img.live.md5",
        "content_xml.msg.emoji.md5",
        "content_xml.msg.emoji.androidmd5",
        "content_xml.msg.emoji.externmd5",
        "content_xml.msg.emoji.live.md5",
    ):
        value = _string(_pick(flat, key)).strip()
        if value and value not in parts:
            parts.append(f'{key.split(".")[-1]}="{value}"')
    compact = "\n".join(part for part in parts if part)
    return compact or content


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    items: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            items[path] = child
            items[key_text] = child
            items.update(_flatten(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            items.update(_flatten(child, f"{prefix}.{index}" if prefix else str(index)))
    return items


def _pick(flat: dict[str, Any], *keys: str) -> Any:
    lower_map = {key.lower(): value for key, value in flat.items()}
    for key in keys:
        if key in flat:
            return flat[key]
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return ""


def _split_group_sender(content: str) -> tuple[str, str]:
    separators = (
        ":\n",
        ":\r\n",
        "：\n",
        "：\r\n",
        r":\n",
        r":\r\n",
        r"：\n",
        r"：\r\n",
        r":\\n",
        r":\\r\\n",
        r"：\\n",
        r"：\\r\\n",
    )
    for separator in separators:
        if separator in content:
            sender, text = content.split(separator, 1)
            sender = sender.strip()
            if sender and len(sender) <= 120:
                return sender, _decode_escaped_text(text)
    return "", content


def _looks_like_wxid(value: str) -> bool:
    return value.startswith(("wxid_", "gh_", "chatroom_")) or value.endswith("@chatroom")


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _string(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _decode_escaped_text(value: str) -> str:
    text = value.replace(r"\n", "\n").replace(r"\r", "\r")
    if r"\u" not in text and r"\x" not in text:
        return text
    try:
        return codecs.decode(text, "unicode_escape")
    except Exception:
        return text


def _extract_revoke_xml_ids(content: str) -> list[str]:
    return [match.group(2).strip() for match in REVOKE_XML_ID_RE.finditer(content or "") if match.group(2).strip()]


def _unique_strings(*values: Any) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = (value,)
        for candidate in candidates:
            text = _string(candidate).strip()
            if text and text not in seen:
                seen.add(text)
                output.append(text)
    return output


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("String", "string", "str", "value", "Value"):
            if key in value:
                return _string(value[key])
        return ""
    return str(value)
