import pytest
import pathlib
from unittest.mock import patch, MagicMock
import os
import json
from app.services.ses import SESService
from botocore.exceptions import ClientError

def test_read_template_success(mock_sts_client):
    """Test successful template reading and markdown conversion."""
    # Sample markdown content
    markdown_content = """Test Subject
This is a **bold** test template.

* Item 1
* Item 2"""
    expected_html = """<p>This is a <strong>bold</strong> test template.</p>
<ul>
<li>Item 1</li>
<li>Item 2</li>
</ul>"""

    # Mock the template file
    mock_template_path = pathlib.Path('app/templates/test_template.md')
    with patch.object(mock_template_path, 'exists', return_value=True), \
         patch.object(mock_template_path, 'read_text', return_value=markdown_content):

        service = SESService()
        subject, text_content, html_content = service._read_template('test_template')

        assert subject == "Test Subject"
        assert text_content == markdown_content.split('\n', 1)[1].strip()
        assert html_content == expected_html

def test_read_template_not_found(mock_sts_client):
    """Test template reading fails when file doesn't exist."""
    mock_template_path = pathlib.Path('app/templates/test_template.md')
    with patch.object(mock_template_path, 'exists', return_value=False):
        service = SESService()
        with pytest.raises(FileNotFoundError, match="Template nonexistent not found"):
            service._read_template('nonexistent')

def test_read_template_empty(mock_sts_client):
    """Test template reading with empty content."""
    mock_template_path = pathlib.Path('app/templates/test_template.md')
    with patch.object(mock_template_path, 'exists', return_value=True), \
         patch.object(mock_template_path, 'read_text', return_value='Test Subject'):

        service = SESService()
        subject, text_content, html_content = service._read_template('empty_template')

        assert subject == "Test Subject"
        assert text_content == ""
        assert html_content == ""

def test_create_or_update_template_new(mock_sts_client):
    """Test creating a new template."""
    markdown_content = """Test Subject
This is a test template."""

    mock_ses = MagicMock()
    mock_ses.get_email_template.side_effect = ClientError(
        {'Error': {'Code': 'NotFoundException'}},
        'GetEmailTemplate'
    )

    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.read_text', return_value=markdown_content), \
         patch('boto3.client', return_value=mock_ses):

        service = SESService()
        result = service.create_or_update_template('test_template')

        assert result is True
        mock_ses.create_email_template.assert_called_once()
        call_args = mock_ses.create_email_template.call_args[1]
        assert call_args['TemplateName'] == 'test_template-test'
        assert call_args['TemplateContent']['Subject'] == 'Test Subject'
        assert 'Text' in call_args['TemplateContent']
        assert 'Html' in call_args['TemplateContent']

def test_create_or_update_template_existing(mock_sts_client):
    """Test updating an existing template."""
    markdown_content = """Test Subject
This is a test template."""

    mock_ses = MagicMock()
    mock_ses.get_email_template.return_value = {'TemplateName': 'test_template-test'}

    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.read_text', return_value=markdown_content), \
         patch('boto3.client', return_value=mock_ses):

        service = SESService()
        result = service.create_or_update_template('test_template')

        assert result is True
        mock_ses.update_email_template.assert_called_once()
        call_args = mock_ses.update_email_template.call_args[1]
        assert call_args['TemplateName'] == 'test_template-test'
        assert call_args['TemplateContent']['Subject'] == 'Test Subject'
        assert 'Text' in call_args['TemplateContent']
        assert 'Html' in call_args['TemplateContent']

def test_send_email_success(mock_sts_client):
    """Test successful email sending with template."""
    mock_ses = MagicMock()
    mock_ses.send_email.return_value = {'MessageId': 'test-message-id'}

    template_data = {
        'user': 'testuser',
        'code': '123456'
    }

    with patch('boto3.client', return_value=mock_ses), \
         patch.dict(os.environ, {'SES_SENDER_EMAIL': 'test@example.com'}):

        service = SESService()
        result = service.send_email(
            to_addresses=['recipient@example.com'],
            template_name='test_template',
            template_data=template_data
        )

        assert result is True
        mock_ses.send_email.assert_called_once()
        call_args = mock_ses.send_email.call_args[1]
        assert call_args['FromEmailAddress'] == 'test@example.com'
        assert call_args['Destination']['ToAddresses'] == ['recipient@example.com']
        assert call_args['Content']['Template']['TemplateName'] == 'test_template-test'
        assert call_args['Content']['Template']['TemplateData'] == json.dumps(template_data)

def test_send_email_failure(mock_sts_client):
    """Test email sending failure."""
    mock_ses = MagicMock()
    mock_ses.send_email.side_effect = ClientError(
        {'Error': {'Code': 'MessageRejected'}},
        'SendEmail'
    )

    with patch('boto3.client', return_value=mock_ses), \
         patch.dict(os.environ, {'SES_SENDER_EMAIL': 'test@example.com'}):

        service = SESService()
        result = service.send_email(
            to_addresses=['recipient@example.com'],
            template_name='test_template',
            template_data={'test': 'data'}
        )

        assert result is False