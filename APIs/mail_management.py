"""Various email related methods"""

import os
import sys
import logging
from typing import Union
from enum import Enum

# pylint: disable=wrong-import-position
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__))))
from Sherlock import Database, WinDRVJenkins
from DatabaseAPI import DBConnector
from jenkins_api import Jenkins

# pylint: enable=wrong-import-position


log = logging.getLogger("MailManagement")

EMAIL_FALLBACK = "wcc.wifi.windows.driver.devops@intel.com"


class Color:
    """Color Definitions"""

    class Blue(Enum):
        """Blue color definitions"""

        BORDER = "#31708f"
        BACKGROUND = "#31708f"
        COLOR = "white"

    class Yellow(Enum):
        """Yellow color definitions"""

        BORDER = "#FFE7A8"
        BACKGROUND = "#FFE7A8"
        COLOR = "#333333"

    class Green(Enum):
        """Green color definitions"""

        BORDER = "#DFF0D8"
        BACKGROUND = "#DFF0D8"
        COLOR = "#333333"

    class Red(Enum):
        """Red color definition"""

        BORDER = "#a94442"
        BACKGROUND = "#a94442"
        COLOR = "white"

    class Grey(Enum):
        """Grey color definition"""

        BORDER = "#C9C9C9"
        BACKGROUND = "#C9C9C9"
        COLOR = "#333333"


def get_mailing_list(mailing_list_name, handle_exceptions=False):
    """
    Args (str, boolean):    mailing_list_name - the name of the mailing list coming from database.
                            handle_exceptions - letting the caller the option to handle any exceptions by himself,
                                                or letting the current function to handle it.
    Returns(list): all contacts in this mailing list. If an error occurs during run: returns 'EMAIL_DEFAULT'
    """

    try:
        db = DBConnector(Database.server, Database.database, Database.username, Database.password)
        mailing_list_contacts = db.definitions.get_mailing_list(mailing_list_name)
    except Exception as ex:
        if handle_exceptions:
            logging.warning("Failed to get mailing list '%s' from database", mailing_list_name)
            warning_subject = "Failed to get mailing list"
            warning_title = f"Failed to get mailing list '{mailing_list_name}' from database"
            warning_message = f"Sending to {EMAIL_FALLBACK} instead.<br><pre>{str(ex)}</pre>"
            send_formatted_email(
                mailing_list=EMAIL_FALLBACK, subject=warning_subject, title=warning_title, message=warning_message
            )
            return EMAIL_FALLBACK
        raise ex

    if not mailing_list_contacts:
        log.warning("Mailing list '%s' does not exists in database.", mailing_list_name)
        if handle_exceptions:
            warning_subject = "Wrong mailing list"
            warning_message = (
                f"WARNING! Mailing list '{mailing_list_name}' "
                f"does not exist in database. Sending instead to {EMAIL_FALLBACK}"
            )
            send_formatted_email(
                mailing_list=EMAIL_FALLBACK, subject=warning_subject, title=warning_subject, message=warning_message
            )
            return EMAIL_FALLBACK
    return mailing_list_contacts


def generate_email_section(title: str, message: str):
    """Generates the HTML to be used in the formatted email message.

    Args:
        title (str): The title - will appear as the title (<h1>) in the body of the email. Supports HTML.
        message (str): The content of the message. Supports HTML.

    Returns:
        str: The HTML content
    """
    section_template = os.path.join(os.path.dirname(__file__), "templates", "emailSection.html")
    with open(section_template, mode="r", encoding="utf-8") as template_file:
        template = template_file.read()

    content = template.replace("{title}", title)
    content = content.replace("{message}", message)
    return content


def generate_email_html(content: str, header: str = None, color: Color = Color.Blue):
    """Generates the Email HTML as it would appear in Outlook.
    Simple takes the content and wraps it in the template - which is just the signature and the wrapping box.

    Args:
        content (str): The HTML content to wrap in the template.
        header (str): Anything you want above the contents (read: above the table) - Defaults to None
        color (Color): The color of the border, image, and headers. Defaults to Blue.
    """
    template_path = os.path.join(os.path.dirname(__file__), "templates", "emailTemplate.html")
    with open(template_path, mode="r", encoding="utf-8") as template_file:
        template = template_file.read()
    template = template.replace("{content}", content)
    template = template.replace("{header}", header if header else "")
    template = template.replace("{img-color}", color.__name__.lower())
    template = template.replace("{border}", color.BORDER.value)
    template = template.replace("{color}", color.COLOR.value)
    template = template.replace("{background}", color.BACKGROUND.value)
    return template


def send_preformatted_email(
    mailing_list: Union[str, list], subject: str, content: str, attachment: str = None, debug=False
):
    """Sends an HTML email without modifying the content.

    Args:
        mailing_list (Union[str, list]): Either a comma separated string or an actual list of target emails
        subject (str): The subject (will appear as subject in Outlook). Has to be Plain-text (no HTML).
        message (str): The content of the message. Supports HTML.
        attachment (str, optional): The path of a file to attach to the email.
        debug (bool): Run in DEBUG mode (with DEBUG logging, DB and jenkins)

    Returns:
        dict: Build properties
    """

    if isinstance(mailing_list, list):
        mailing_list = ",".join(mailing_list)

    params = {"CONTENT": content, "SUBJECT": subject, "MAILING_LIST": mailing_list}
    if attachment and not os.path.exists(attachment):
        raise FileNotFoundError(f"Attachment file not found: {attachment}")
    file_params = {"ATTACHMENT": attachment} if attachment else None
    if "JENKINS_URL" in os.environ:
        jenkins_auth = WinDRVJenkins.get_instance_from_url(os.environ.get("JENKINS_URL"))
        jenkins = Jenkins(jenkins_auth.url, jenkins_auth.username, jenkins_auth.token)
    else:
        log.info("Jenkins instance not found by the environment variable. Init Jenkins instance from Sherlock.")
        jenkins_auth = WinDRVJenkins.Pre if debug else WinDRVJenkins.Prod
        jenkins = Jenkins(jenkins_auth.url, jenkins_auth.username, jenkins_auth.token)
    return jenkins.trigger_build("SendEmailHTML", build_params=params, file_params=file_params)


def send_formatted_email(
    mailing_list: Union[str, list],
    subject: str,
    title: str,
    message: str,
    color: Color = Color.Blue,
    attachment: str = None,
    debug=False,
):
    """Sends an HTML email formatted with the generic Sherlock/WCD DevOps email template.
    Auto generates a single email section with a title and a content.

    Args:
        mailing_list (Union[str, list]): Either a comma separated string or an actual list of target emails
        subject (str): The subject (will appear as subject in Outlook). Has to be Plain-text (no HTML).
        title (str): The title - will appear as the title (as `<h1>`) in the body of the email. Supports HTML.
        message (str): The content of the message. Supports HTML.
        color (Color): The color of the border, image, and headers. Defaults to Blue.
        attachment (str, optional): The path of a file to attach to the email.
        debug (bool): Run in DEBUG mode (with DEBUG logging, DB and jenkins)

    Returns:
        dict: Build properties
    """
    content = generate_email_section(title, message)
    send_preformatted_email(mailing_list, subject, generate_email_html(content, None, color), attachment, debug)
