from app.services.litellm import LiteLLMService


def test_sanitize_alias_basic():
    """Test basic sanitization of key_alias"""
    assert LiteLLMService.sanitize_alias("my-key") == "my-key"
    assert LiteLLMService.sanitize_alias("my key") == "my_key"
    # @ is replaced with _at_
    assert LiteLLMService.sanitize_alias("my@key") == "my_at_key"
    assert LiteLLMService.sanitize_alias("@mykey") == "at_mykey"
    assert LiteLLMService.sanitize_alias("mykey@") == "mykey_at"
    assert LiteLLMService.sanitize_alias("@mykey@") == "at_mykey_at"


def test_sanitize_alias_email():
    """Test sanitization of email-based alias"""
    # @ is replaced with _at_
    assert LiteLLMService.sanitize_alias("test@example.com") == "test_at_example.com"
    assert (
        LiteLLMService.sanitize_alias("test@example.com - my-key")
        == "test_at_example.com_-_my-key"
    )


def test_sanitize_alias_special_chars():
    """Test sanitization of special characters"""
    # @ is replaced with _at_, other special chars are replaced with _
    assert LiteLLMService.sanitize_alias("key!#$%^&*()") == "key"
    assert LiteLLMService.sanitize_alias("key!@#$%^&*()") == "key_at"
    assert (
        LiteLLMService.sanitize_alias("key_with.dots/and-dashes")
        == "key_with.dots/and-dashes"
    )


def test_sanitize_alias_start_end_alphanumeric():
    """Test that alias starts and ends with alphanumeric characters"""
    assert LiteLLMService.sanitize_alias("_my_key_") == "my_key"
    assert LiteLLMService.sanitize_alias("-my-key-") == "my-key"
    assert LiteLLMService.sanitize_alias(".my.key.") == "my.key"
    assert LiteLLMService.sanitize_alias("/my/key/") == "my/key"


def test_sanitize_alias_length():
    """Test that alias length rules are followed"""
    # Too short
    assert LiteLLMService.sanitize_alias("a") == ""
    # "@" is replaced with "at" (length 2)
    assert LiteLLMService.sanitize_alias("@") == "at"
    assert LiteLLMService.sanitize_alias("---") == ""
    # Just enough (minimum acceptable length after sanitization is 2)
    assert LiteLLMService.sanitize_alias("ab") == "ab"
    # Too long (should be truncated to 255 characters)
    long_alias = "a" * 256
    sanitized = LiteLLMService.sanitize_alias(long_alias)
    assert len(sanitized) == 255
    assert sanitized == "a" * 255

def test_sanitize_alias_collapse_underscores():
    """Test collapsing of multiple underscores"""
    assert LiteLLMService.sanitize_alias("my   key") == "my_key"
    # @ is replaced with _at_
    assert LiteLLMService.sanitize_alias("my@@@key") == "my_at_at_at_key"
    assert LiteLLMService.sanitize_alias("my!!!key") == "my_key"
