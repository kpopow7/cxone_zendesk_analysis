from __future__ import annotations

from orchestration.chatbot.settings import ChatbotSettings


def load_chatbot_auth_users(settings: ChatbotSettings) -> list[tuple[str, str]]:
    """Return Gradio auth tuples (username, password) for company-only access."""
    users: list[tuple[str, str]] = []

    if settings.chatbot_users:
        for entry in settings.chatbot_users.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            username, password = entry.split(":", 1)
            username = username.strip()
            password = password.strip()
            if username and password:
                users.append((username, password))

    if settings.chatbot_username and settings.chatbot_password:
        pair = (settings.chatbot_username.strip(), settings.chatbot_password.strip())
        if pair not in users and pair[0] and pair[1]:
            users.append(pair)

    if not users:
        raise RuntimeError(
            "No chatbot login configured. Set CHATBOT_USERS (user:pass,user2:pass2) "
            "or CHATBOT_USERNAME and CHATBOT_PASSWORD."
        )
    return users
