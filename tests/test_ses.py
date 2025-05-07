import pytest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timedelta, UTC
import os
import pathlib
from app.services.ses import SESService

def test_read_template_success(mock_sts_client):
    """Test successful template reading and markdown conversion."""
    # Sample markdown content
    markdown_content = """# Test Template
This is a **bold** test template.

* Item 1
* Item 2"""
    expected_html = """<h1>Test Template</h1>
<p>This is a <strong>bold</strong> test template.</p>
<ul>
<li>Item 1</li>
<li>Item 2</li>
</ul>"""

    # Mock the template file
    mock_template_path = pathlib.Path('app/templates/test_template.txt')
    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.read_text', return_value=markdown_content):

        service = SESService()
        text_content, html_content = service._read_template('test_template')

        assert text_content == markdown_content
        assert html_content == expected_html

def test_read_template_not_found(mock_sts_client):
    """Test template reading fails when file doesn't exist."""
    with patch('pathlib.Path.exists', return_value=False):
        service = SESService()
        with pytest.raises(FileNotFoundError, match="Template nonexistent not found"):
            service._read_template('nonexistent')

def test_read_template_empty(mock_sts_client):
    """Test template reading with empty content."""
    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.read_text', return_value=''):

        service = SESService()
        text_content, html_content = service._read_template('empty_template')

        assert text_content == ''
        assert html_content == ''

def test_read_template_special_chars(mock_sts_client):
    """Test template reading with special characters and markdown formatting."""
    markdown_content = """# Special Characters
*Italic* and **bold** text
> Blockquote

`code`"""

    expected_html = """<h1>Special Characters</h1>\n<p><em>Italic</em> and <strong>bold</strong> text</p>\n<blockquote>\n<p>Blockquote</p>\n</blockquote>\n<p><code>code</code></p>"""

    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.read_text', return_value=markdown_content):

        service = SESService()
        text_content, html_content = service._read_template('special_chars')

        assert text_content == markdown_content
        assert html_content == expected_html