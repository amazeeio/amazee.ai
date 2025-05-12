import boto3
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any, Tuple
import os
import pathlib
import markdown
import logging
import json
from app.services import aws_auth

# Get role name from environment variable
role_name = os.getenv('SES_ROLE_NAME')
env_suffix = os.getenv('ENV_SUFFIX')
ses_region = os.getenv('SES_REGION', "eu-central-1") # SES defaults to Frankfurt as Zurich is not available

# Set up logging
logger = logging.getLogger(__name__)

class SESService:
    def __init__(
        self,
        session_name: str = "SESServiceSession"
    ):
        """
        Initialize the SES service with temporary credentials from STS AssumeRole.

        Args:
            session_name (str): Name for the assumed role session. Defaults to "SESServiceSession".

        Raises:
            ValueError: If SES_ROLE_NAME or AWS_REGION environment variables are not set
        """
        if not role_name:
            raise ValueError(
                "SES_ROLE_NAME environment variable is not set. "
                "Please set it to the name of the IAM role to assume."
            )

        # Get region from environment variable
        if not ses_region:
            raise ValueError(
                "SES_REGION environment variable is not set. "
                "Please set it to your AWS region (e.g., eu-central-1)."
            )

        self.templates_dir = pathlib.Path(__file__).parent.parent / "templates"

        # Create SESv2 client with temporary credentials
        self.ses = boto3.client(
            'sesv2',
            region_name=ses_region,
            **aws_auth.get_credentials(role_name, ses_region)
        )

    def _read_template(self, template_name: str) -> Tuple[str, str, str]:
        """
        Read a template file from the templates directory and convert markdown to HTML.
        The template file should be written in markdown format.
        The first line of the file will be used as the email subject.

        Args:
            template_name (str): Name of the template file (without extension)

        Returns:
            Tuple[str, str, str]: A tuple containing (subject, text_content, html_content)
                - subject: The first line of the template file
                - text_content: The original markdown content
                - html_content: The markdown content converted to HTML

        Raises:
            FileNotFoundError: If the template file doesn't exist
        """
        template_path = self.templates_dir / f"{template_name}.md"
        if not template_path.exists():
            raise FileNotFoundError(f"Template {template_name} not found")

        # Read the markdown content
        content = template_path.read_text()

        # Split into subject and body
        lines = content.split('\n', 1)
        subject = lines[0].strip()
        markdown_content = lines[1].strip() if len(lines) > 1 else ""

        # Convert markdown to HTML with necessary extensions
        html_content = markdown.markdown(
            markdown_content,
            extensions=[
                'markdown.extensions.tables',
                'markdown.extensions.fenced_code',
                'markdown.extensions.sane_lists',
                'markdown.extensions.nl2br'
            ],
            output_format='html5'
        )

        return subject, markdown_content, html_content

    @aws_auth.ensure_valid_credentials(role_name=role_name, region_name=ses_region)
    def get_template(self, template_name: str) -> Optional[Dict[str, Any]]:
        """
        Get an existing SES template.

        Args:
            template_name (str): Name of the template to retrieve

        Returns:
            Optional[Dict[str, Any]]: The template if it exists, None otherwise
        """
        try:
            response = self.ses.get_email_template(TemplateName=f"{template_name}-{env_suffix}")
            return response
        except ClientError as e:
            if e.response['Error']['Code'] == 'NotFoundException':
                return None
            raise e

    @aws_auth.ensure_valid_credentials(role_name=role_name, region_name=ses_region)
    def create_or_update_template(self, template_name: str) -> bool:
        """
        Create or update an SES template using the content from the corresponding text file.
        This should be called during system startup to ensure templates are in sync.
        The template file should be written in markdown format, with the first line being the subject.

        Args:
            template_name (str): Name of the template (without extension)

        Returns:
            bool: True if template was created/updated successfully, False otherwise
        """
        try:
            # Read template content and convert to HTML
            subject, text_content, html_content = self._read_template(template_name)

            template_data = {
                'TemplateName': f"{template_name}-{env_suffix}",
                'TemplateContent': {
                    'Subject': subject,
                    'Text': text_content,
                    'Html': html_content
                }
            }

            # Check if template exists
            existing_template = self.get_template(f"{template_name}")

            if existing_template:
                # Update existing template
                self.ses.update_email_template(**template_data)
                print(f"Updated email template: {template_name}-{env_suffix}")
            else:
                # Create new template
                self.ses.create_email_template(**template_data)
                print(f"Created new email template: {template_name}-{env_suffix}")

            return True

        except (ClientError, FileNotFoundError) as e:
            print(f"Error managing template {template_name}-{env_suffix}: {str(e)}")
            # logger.error(f"Error managing template {template_name}-{env_suffix}: {str(e)}", exc_info=True)
            return False

    @aws_auth.ensure_valid_credentials(role_name=role_name, region_name=ses_region)
    def send_email(
        self,
        to_addresses: list[str],
        template_name: str,
        template_data: Dict[str, Any],
        from_address: Optional[str] = None
    ) -> bool:
        """
        Send an email using an SES template.

        Args:
            to_addresses (list[str]): List of recipient email addresses
            template_name (str): Name of the template to use
            template_data (Dict[str, Any]): Data to populate the template with
            from_address (Optional[str]): Sender email address. If not provided, uses the verified sender.

        Returns:
            bool: True if email was sent successfully, False otherwise
        """
        try:
            # Get the sender email address
            if not from_address:
                from_address = os.getenv('SES_SENDER_EMAIL')
                if not from_address:
                    raise ValueError("SES_SENDER_EMAIL environment variable is not set")

            # Convert template_data to JSON string
            template_data_json = json.dumps(template_data)

            # Send the email using the template
            response = self.ses.send_email(
                FromEmailAddress=from_address,
                Destination={
                    'ToAddresses': to_addresses
                },
                Content={
                    'Template': {
                        'TemplateName': f"{template_name}-{env_suffix}",
                        'TemplateData': template_data_json
                    }
                }
            )
            logger.info(f"Successfully sent email using template {template_name}-{env_suffix} to {to_addresses}, message ID: {response['MessageId']}")
            return True

        except ClientError as e:
            logger.error(f"Error sending email using template {template_name}-{env_suffix}: {str(e)}", exc_info=True)
            return False
