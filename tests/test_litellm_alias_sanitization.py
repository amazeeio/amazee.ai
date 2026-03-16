from app.services.litellm import LiteLLMService


def test_sanitize_alias_basic():
    """Test basic sanitization of key_alias"""
    assert LiteLLMService.sanitize_alias("my-key") == "my-key"
    assert LiteLLMService.sanitize_alias("my key") == "my_key"
    # @ is allowed in the middle, but replaced at start/end
    assert LiteLLMService.sanitize_alias("my@key") == "my@key"
    assert LiteLLMService.sanitize_alias("@mykey") == "mykey"
    assert LiteLLMService.sanitize_alias("mykey@") == "mykey"
    assert LiteLLMService.sanitize_alias("@mykey@") == "mykey"


def test_sanitize_alias_email():
    """Test sanitization of email-based alias"""
    # @ is allowed in the middle
    assert LiteLLMService.sanitize_alias("test@example.com") == "test@example.com"
    assert (
        LiteLLMService.sanitize_alias("test@example.com - my-key")
        == "test@example.com_-_my-key"
    )


def test_sanitize_alias_special_chars():
    """Test sanitization of special characters"""
    # @ is allowed, but other special chars are replaced
    assert LiteLLMService.sanitize_alias("key!#$%^&*()") == "key"
    assert LiteLLMService.sanitize_alias("key!@#$%^&*()") == "key_@"
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
    assert LiteLLMService.sanitize_alias("@") == ""
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
    # @ in the middle is kept, only @ at start/end is replaced
    assert LiteLLMService.sanitize_alias("my@@@key") == "my@@@key"
    assert LiteLLMService.sanitize_alias("my!!!key") == "my_key"
