"""Outbound email helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging

import requests

from .config import Settings


logger = logging.getLogger(__name__)


class EmailSender(ABC):
    @abstractmethod
    def send_password_reset(self, *, email: str, reset_link: str) -> None:
        raise NotImplementedError


class ResendEmailSender(EmailSender):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_password_reset(self, *, email: str, reset_link: str) -> None:
        if not self._settings.resend_api_key:
            raise RuntimeError("Resend is not configured")
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self._settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": self._settings.resend_from_email,
                "to": [email],
                "subject": "Reset your Paperbase password",
                "html": (
                    "<p>You asked to reset your Paperbase password.</p>"
                    f"<p><a href=\"{reset_link}\">Reset your password</a></p>"
                    "<p>If you did not request this, you can ignore this email.</p>"
                ),
            },
            timeout=30,
        )
        response.raise_for_status()
        logger.info("Sent password reset email", extra={"event": "auth.password_reset.sent", "email": email})
