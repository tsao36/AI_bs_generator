"""A simple API Wrapper for the ChAI and Azure AI APIs"""

import os
import sys
import time
import json
import logging
import requests
from requests import Response
from langchain_openai import AzureChatOpenAI as _azure_client
from langchain.schema import SystemMessage, HumanMessage

# pylint: disable=wrong-import-position
sys.path.append(os.path.dirname(__file__))  # Add own dir
from secret_manager import SecretManager
import Sherlock

# pylint: enable=wrong-import-position


log = logging.getLogger("AIBot")


class _AIBase:
    """A simple AI Bot interface.
    Subclasses should implement the respond method.
    """

    def _get_key(self, username: str, service: str) -> str:
        if "PAM_CERT" not in os.environ:
            raise EnvironmentError("PAM_CERT environment variable not found!")

        secret_mgmt = SecretManager(Sherlock.PAM.cert_appid, Sherlock.PAM.safe_name, os.environ["PAM_CERT"])
        return secret_mgmt.get_secret(username, service)

    def send_query(self, query: str) -> str:
        """Send a query to the AI API

        Args:
            query (str): The query to send

        Returns:
            str: The response

        Raises:
            HTTPError: If the request was unsuccessful
        """
        raise NotImplementedError("Subclasses should implement this!")


class ChAI(_AIBase):
    """ChAI API Wrapper.
    Authenticates using PAM, by getting the "ChAI" secret from PAM safe.
    Starts a single chat session with the specified assistant.
    Any subsequent queries will be sent to the same chat session and assistant.

    Args:
        assistant_name (str): The name of the assistant to use.
                              You can get a list of available assistants with the static function ChAI.get_assistants()
                              Defaults to `GPT 4o Enterprise`.
        timeout (int, optional): The timeout for requests. Defaults to 180.

    Raises:
        EnvironmentError: If the PAM_CERT environment variable is not found
        ValueError: If the assistant does not exist
        HTTPError: If initiating the chat session was unsuccessful
    """

    def __init__(self, assistant_name: str = "GPT 4o Enterprise", timeout: int = 180):
        self.timeout = timeout
        self.token = self._get_key(Sherlock.ChAI.username, "ChAI")
        self.base_url = Sherlock.ChAI.base_url
        self.chat_id = ""

        # Check if assistant exists
        log.info("Getting assistant '%s'...", assistant_name)
        if assistant_name not in (assistants := ChAI.get_assistants()):
            raise ValueError(
                f"Assistant '{assistant_name}' does not exist! " f"Possible assistants are: {', '.join(assistants)}"
            )

        # Set ChatID
        log.info("Starting chat session...")
        response = self._post("ask-ChAI", {"queries": ["Hello"]})
        response.raise_for_status()
        self.chat_id = response.json()["chat_id"]
        log.info("Assistant '%s' is ready!", assistant_name)

    def _get(self, endpoint: str) -> Response:
        return requests.get(self.base_url + endpoint, timeout=self.timeout)

    def _post(self, endpoint: str, data: dict) -> Response:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary!")

        data = {"input": json.dumps({"chat_id": self.chat_id, "client_secret": self.token, **data})}

        return requests.post(self.base_url + endpoint, json=data, timeout=self.timeout)

    @staticmethod
    def get_assistants() -> list:
        """Get a list of available assistants

        Returns:
            list: List of available assistants
        """
        response = requests.get(Sherlock.ChAI.base_url + "get-predefined-assistants", timeout=180)
        response.raise_for_status()
        return list(response.json().keys())

    def send_query(self, query: str) -> str:
        """Send a query to the ChAI API

        Args:
            query (str): The query to send

        Returns:
            str: The response

        Raises:
            HTTPError: If the request was unsuccessful
        """
        response = self._post("ask-ChAI", {"queries": [query]})
        response.raise_for_status()
        return response.json()["answers"][0]


class AzureOpenAI(_AIBase):
    """Azure OpenAI API Wrapper.
    Authenticates using PAM, by getting the "Azure-OpenAI" secret from PAM safe.

    Args:
        azure_deployment (str): The name of the deployment to use.
        temperature (float): The temperature to use for the model (how 'aggressive' the model is). Defaults to `0.7`.
        system_instructions (str, optional): System instructions to provide to the assistant.
                                             Defaults to `None`.

    Raises:
        EnvironmentError: If the `PAM_CERT` environment variable is not found
    """

    def __init__(self, azure_deployment: str = "gpt-4o", temperature: float = 0.7, system_instructions: str = None):
        # Check for proxy settings
        if any(proxy in os.environ for proxy in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]):
            no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy"))
            if not no_proxy or ".openai.azure.com" not in no_proxy:
                raise EnvironmentError(
                    "Proxy detected without proper exceptions! This may cause issues with the Azure API!"
                    "Please add '.openai.azure.com' to the NO_PROXY environment variable."
                )

        self._azure = _azure_client(
            azure_deployment=azure_deployment,
            temperature=temperature,
            openai_api_key=self._get_key(Sherlock.AzureOpenAI.resource_group, "Azure-OpenAI"),
            azure_endpoint=Sherlock.AzureOpenAI.endpoint,
            api_version=Sherlock.AzureOpenAI.api_version,
        )
        self.system_instructions = system_instructions

    def send_query(self, query: str, system_instructions: str = None) -> str:
        """Send a query to the AI API

        Args:
            query (str): The query to send
            system_instructions (str, optional): System instructions to provide to the assistant in additional to the
                                                 instructions provided on init (if any). Defaults to `None`.

        Returns:
            str: The response
        """
        messages = []
        if self.system_instructions:
            messages.append(SystemMessage(self.system_instructions))
        if system_instructions:
            messages.append(SystemMessage(system_instructions))
        messages.append(HumanMessage(query))

        response = ""
        for chunk in self._azure.stream(messages):
            response += chunk.content
            time.sleep(0.02)  # Sleep for 20ms to avoid rate limiting
        return response
