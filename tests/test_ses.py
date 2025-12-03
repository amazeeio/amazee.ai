import pytest
from unittest.mock import patch, MagicMock
import os
import json
from app.services.ses import SESService
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, UTC

@pytest.fixture
def mock_templates_dir():
    """Fixture to mock the templates directory."""
    mock_dir = MagicMock()
    mock_file = MagicMock()
    mock_dir.__truediv__.return_value = mock_file
    return mock_dir, mock_file

def test_read_template_success(mock_sts_client, mock_templates_dir):
    """Test successful template reading and markdown conversion."""
    mock_dir, mock_file = mock_templates_dir

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

    mock_file.exists.return_value = True
    mock_file.read_text.return_value = markdown_content

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'):

        service = SESService()
        service.templates_dir = mock_dir

        subject, text_content, html_content = service._read_template('test_template')

        assert subject == "Test Subject"
        assert text_content == markdown_content.split('\n', 1)[1].strip()
        assert html_content == expected_html

def test_read_template_not_found(mock_sts_client, mock_templates_dir):
    """Test template reading fails when file doesn't exist."""
    mock_dir, mock_file = mock_templates_dir
    mock_file.exists.return_value = False

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'):

        service = SESService()
        service.templates_dir = mock_dir

        with pytest.raises(FileNotFoundError, match="Template nonexistent not found"):
            service._read_template('nonexistent')

def test_read_template_empty(mock_sts_client, mock_templates_dir):
    """Test template reading with empty content."""
    mock_dir, mock_file = mock_templates_dir
    mock_file.exists.return_value = True
    mock_file.read_text.return_value = 'Test Subject'

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'):

        service = SESService()
        service.templates_dir = mock_dir

        subject, text_content, html_content = service._read_template('empty_template')

        assert subject == "Test Subject"
        assert text_content == ""
        assert html_content == ""

def test_create_or_update_template_new(mock_sts_client, mock_templates_dir):
    """Test creating a new template."""
    mock_dir, mock_file = mock_templates_dir

    markdown_content = """Test Subject
This is a test template."""

    mock_file.exists.return_value = True
    mock_file.read_text.return_value = markdown_content

    mock_ses = MagicMock()
    mock_ses.get_email_template.side_effect = ClientError(
        {'Error': {'Code': 'NotFoundException'}},
        'GetEmailTemplate'
    )

    # Mock STS for auth
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
    mock_sts.assume_role.return_value = {
        'Credentials': {
            'AccessKeyId': 'test-access-key',
            'SecretAccessKey': 'test-secret-key',
            'SessionToken': 'test-session-token',
            'Expiration': datetime.now(UTC) + timedelta(hours=1)
        }
    }

    def boto3_side_effect(service_name, **kwargs):
        if service_name == 'sesv2':
            return mock_ses
        if service_name == 'sts':
            return mock_sts
        return MagicMock()

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'), \
         patch('app.services.ses.env_suffix', 'test'), \
         patch('boto3.client', side_effect=boto3_side_effect):

        service = SESService()
        service.templates_dir = mock_dir

        result = service.create_or_update_template('test_template')

        assert result is True
        mock_ses.create_email_template.assert_called_once()
        call_args = mock_ses.create_email_template.call_args[1]
        assert call_args['TemplateName'] == 'test_template-test'
        assert call_args['TemplateContent']['Subject'] == 'Test Subject'
        assert 'Text' in call_args['TemplateContent']
        assert 'Html' in call_args['TemplateContent']

def test_create_or_update_template_existing(mock_sts_client, mock_templates_dir):
    """Test updating an existing template."""
    mock_dir, mock_file = mock_templates_dir

    markdown_content = """Test Subject
This is a test template."""

    mock_file.exists.return_value = True
    mock_file.read_text.return_value = markdown_content

    mock_ses = MagicMock()
    mock_ses.get_email_template.return_value = {'TemplateName': 'test_template-test'}

    # Mock STS for auth
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
    mock_sts.assume_role.return_value = {
        'Credentials': {
            'AccessKeyId': 'test-access-key',
            'SecretAccessKey': 'test-secret-key',
            'SessionToken': 'test-session-token',
            'Expiration': datetime.now(UTC) + timedelta(hours=1)
        }
    }

    def boto3_side_effect(service_name, **kwargs):
        if service_name == 'sesv2':
            return mock_ses
        if service_name == 'sts':
            return mock_sts
        return MagicMock()

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'), \
         patch('app.services.ses.env_suffix', 'test'), \
         patch('boto3.client', side_effect=boto3_side_effect):

        service = SESService()
        service.templates_dir = mock_dir

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

    # Mock STS for auth
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
    mock_sts.assume_role.return_value = {
        'Credentials': {
            'AccessKeyId': 'test-access-key',
            'SecretAccessKey': 'test-secret-key',
            'SessionToken': 'test-session-token',
            'Expiration': datetime.now(UTC) + timedelta(hours=1)
        }
    }

    def boto3_side_effect(service_name, **kwargs):
        if service_name == 'sesv2':
            return mock_ses
        if service_name == 'sts':
            return mock_sts
        return MagicMock()

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'), \
         patch('app.services.ses.env_suffix', 'test'), \
         patch('boto3.client', side_effect=boto3_side_effect), \
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

    # Mock STS for auth
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
    mock_sts.assume_role.return_value = {
        'Credentials': {
            'AccessKeyId': 'test-access-key',
            'SecretAccessKey': 'test-secret-key',
            'SessionToken': 'test-session-token',
            'Expiration': datetime.now(UTC) + timedelta(hours=1)
        }
    }

    def boto3_side_effect(service_name, **kwargs):
        if service_name == 'sesv2':
            return mock_ses
        if service_name == 'sts':
            return mock_sts
        return MagicMock()

    with patch('app.services.ses.role_name', 'test-role'), \
         patch('app.services.ses.ses_region', 'eu-central-1'), \
         patch('app.services.ses.env_suffix', 'test'), \
         patch('boto3.client', side_effect=boto3_side_effect), \
         patch.dict(os.environ, {'SES_SENDER_EMAIL': 'test@example.com'}):

        service = SESService()
        result = service.send_email(
            to_addresses=['recipient@example.com'],
            template_name='test_template',
            template_data={'test': 'data'}
        )

        assert result is False