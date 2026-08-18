def normalize_email_for_lookup(email: str) -> str:
    parts = email.lower().rsplit("@", 1)
    if len(parts) == 2:
        local_part = parts[0].split("+")[0]
        return f"{local_part}@{parts[1]}"
    return email.lower()
