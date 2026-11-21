def normalize_email_for_lookup(email: str) -> str:
    parts = email.lower().rsplit("@", 1)
    if len(parts) == 2:
        local_part = parts[0].split("+")[0]
        return f"{local_part}@{parts[1]}"
    return email.lower()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def base_mailbox(email: str) -> str:
    """The inbox an address delivers to: Gmail ignores dots and googlemail.com.
    Kept apart from normalize_email_for_lookup, which logins and user lookups use,
    so Gmail users keep the address they signed up with."""
    normalized = normalize_email_for_lookup(email)
    if "@" not in normalized:
        return normalized
    local_part, domain = normalized.rsplit("@", 1)
    if domain in GMAIL_DOMAINS:
        return f"{local_part.replace('.', '')}@gmail.com"
    return normalized
