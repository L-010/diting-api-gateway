"""平台内置邮件模板；调用方只传递经过业务校验的用户参数。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailContent:
    subject: str
    body_text: str


def _message(*, greeting: str, lines: list[str], action_url: str = "", expiry: str = "", brand_name: str = "API Gateway") -> str:
    parts = [greeting, "", *lines]
    if action_url:
        parts.extend(["", "操作地址：", action_url])
    if expiry:
        parts.extend(["", f"该链接有效期为 {expiry}，并且只能使用一次。"])
    parts.extend(["", "如果这不是你的操作，请忽略此邮件。", "", brand_name])
    return "\n".join(parts)


def registration_verification(display_name: str, action_url: str, *, resent: bool = False, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="重新验证 API Gateway 开发者账号邮箱" if resent else "验证 API Gateway 开发者账号邮箱",
        body_text=_message(
            greeting=f"你好，{display_name}：",
            lines=["请验证邮箱以完成注册申请。验证成功后，账号才会进入管理员审批队列。"],
            action_url=action_url,
            expiry="24 小时", brand_name=brand_name,
        ),
    )


def password_reset(display_name: str, action_url: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="重置 API Gateway 开发者账号密码",
        body_text=_message(
            greeting=f"你好，{display_name}：",
            lines=["我们收到了你的密码重置申请。请通过以下地址设置新密码。"],
            action_url=action_url,
            expiry="30 分钟", brand_name=brand_name,
        ),
    )


def email_change(display_name: str, action_url: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="确认更换 API Gateway 账号邮箱",
        body_text=_message(
            greeting=f"你好，{display_name}：",
            lines=["请确认将此邮箱设为新的登录邮箱。完成验证前，原邮箱继续有效。"],
            action_url=action_url,
            expiry="24 小时", brand_name=brand_name,
        ),
    )


def account_approved(display_name: str, login_url: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="API Gateway 开发者账号已审批通过",
        body_text=_message(
            greeting=f"你好，{display_name}：",
            lines=["你的注册申请已审批通过，现在可以登录平台并创建全局 API Key。"],
            action_url=login_url, brand_name=brand_name,
        ),
    )


def account_rejected(display_name: str, reason: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="API Gateway 开发者账号申请结果",
        body_text=_message(greeting=f"你好，{display_name}：", lines=["你的注册申请未通过。", f"原因：{reason}"], brand_name=brand_name),
    )


def account_disabled(display_name: str, reason: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="API Gateway 开发者账号已停用",
        body_text=_message(greeting=f"你好，{display_name}：", lines=["你的账号已被管理员停用。", f"原因：{reason}"], brand_name=brand_name),
    )


def smtp_test(recipient: str, *, brand_name: str = "API Gateway") -> EmailContent:
    return EmailContent(
        subject="API Gateway 邮件服务测试",
        body_text=_message(
            greeting="你好：", brand_name=brand_name,
            lines=[f"这是一封发送到 {recipient} 的平台邮件配置测试邮件。", "收到此邮件表示 SMTP 配置和邮件 Outbox 投递链路工作正常。"],
        ),
    )
