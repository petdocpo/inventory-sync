"""
Power Automate Webhook 통신으로 알림을 전송하는 모듈.
Teams 채널로 재고 불일치 알림을 푸시하는 기능을 담당.
Power Automate Webhook URL을 설정하여 알림을 전송.
"""
from typing import Any, Dict


def send_notification(webhook_url: str, message: Dict[str, Any]) -> None:
    """
    Power Automate Webhook을 통해 알림 메시지를 전송합니다.

    Args:
        webhook_url: Power Automate Webhook의 URL
        message: 알림 메시지 내용
    """
    pass