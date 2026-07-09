"""
An API wrapper for Confluence WIKI
"""

import os
import logging
from typing import List, Union
from enum import Enum
from datetime import datetime
from requests import Session

logging.basicConfig(format="%(asctime)s [%(levelname)s][%(name)s] %(message)s")
log = logging.getLogger("WikiAPI")


class Wiki:
    """
    A Confluence Wikipedia API Instance

    Args:
        base_url (str): The base URL of the Instance (most likely something like https://wiki.ith.intel.com/)
        auth_token (str): The authentication token to use (you can generate one at
                          https://wiki.ith.intel.com/plugins/personalaccesstokens/usertokens.action
        verify (bool|str, optional): Whether to verify the SSL certificate of the API. Defaults to True.
                                     Uses system certificate if `pip-system-certs` is installed and set to True.
                                     Can also be a path to a pem file for private certs.
    """

    def __init__(self, base_url, auth_token, verify=True):
        self.session = Session()
        self.session.verify = verify
        self.session.headers = {"Authorization": f"Bearer {auth_token}"}
        self.base_url = base_url + "rest/"

    def _rest(self, method, endpoint, data=None, params=None):
        response = self.session.request(method, self.base_url + endpoint, data=data, params=params)
        if not response.ok:
            log.error(response.text)
            response.raise_for_status()
        return response

    def get(self, endpoint, params: None):
        """Perform a GET REST Call

        Args:
            endpoint (str): The API endpoint (not including the base URL and the `rest/` part)
            params (dict, optional): The parameters to attach to the GET request (if any)

        Raises:
            HTTPError: If the response is not in the OK range (200-399)

        Returns:
            Response: The HTTP Response
        """
        return self._rest("GET", endpoint, params=params)

    def put(self, endpoint, data=None):
        """Perform a PUT REST Call

        Args:
            endpoint (str): The API endpoint (not including the base URL and the `rest/` part)
            params (dict, optional): The data to attach to the PUT request (if any)

        Raises:
            HTTPError: If the response is not in the OK range (200-399)

        Returns:
            Response: The HTTP Response
        """
        return self._rest("PUT", endpoint, data=data)

    def query_users(self, query: str) -> list:
        """Given a query, returns all the users that match that query

        Args:
            query (str): The query to use for search

        Returns:
            list: A list of all user objects
        """
        return self.get("prototype/1/search/user.json", {"query": query}).json()["result"]


class Calendar:
    """An instance of the Confluence Wiki Calendar
    Args:
        wiki_instance (Wiki): The Wiki instance this Calendar is part of
        calendar_id (str): The ID of the calendar (the GUID part of its embed url)
        timezone (str, optional): The default timezone of the calendar. Defaults to "Asia/Jerusalem".
    """

    _SYSTEM_NOPADDING = "#" if os.name == "nt" else "-"
    DATE_FORMAT = rf"%{_SYSTEM_NOPADDING}d %b %Y"
    TIME_FORMAT = rf"%{_SYSTEM_NOPADDING}H:%M %p"

    class ReoccurrenceTypes(Enum):
        """All the types of reoccurring events"""

        DAILY = "DAILY"
        WEEKLY = "WEEKLY"
        MONTHLY = "MONTHLY"
        YEARLY = "YEARLY"

    class ReoccurDays(Enum):
        """The RRule shorthand for the weekdays"""

        SUNDAY = "SU"
        MONDAY = "MO"
        TUESDAY = "TU"
        WEDNESDAY = "WE"
        THURSDAY = "TH"
        FRIDAY = "FR"
        SATURDAY = "SA"

    def __init__(self, wiki_instance: Wiki, calendar_id: str, timezone="Asia/Jerusalem"):
        self.wiki_instance = wiki_instance
        self.calendar_id = calendar_id
        self.timezone = timezone
        self._event_template = {
            "confirmRemoveInvalidUsers": "false",
            "isSingleJiraDate": "false",
            "subCalendarId": calendar_id,
            "editAllInRecurrenceSeries": "true",
            "userTimeZoneId": timezone,
        }

    def add_event(
        self,
        event_type: str,
        title: str,
        start_date: datetime,
        end_date: datetime,
        description: str = None,
        person: str = None,
        all_day_event=True,
        reoccur_type: ReoccurrenceTypes = None,
        reoccur_interval: int = 1,
        reoccur_days: List[ReoccurDays] = None,
        reoccur_until: Union[datetime, int] = None,
        timezone_override=None,
    ):
        """Add a new event to the calendar

        Args:
            event_type (str): The event type (same as its "category" name basically)
            person (str): An attendee (optional for some event types, not for others)
            title (str): The event title
            description (str): The event description (optional)
            start_date (datetime): The start datetime of the event
            end_date (datetime): The end datetime of the event
            all_day_event (bool, optional): Whether this counts as an all-day event. Defaults to True.
            reoccur_type (ReoccurrenceTypes, optional): Which type of reoccurrence the event has. Defaults to None.
            reoccur_interval (int, optional): The interval between reoccurrences. (every N weeks/days/etc.)
                                              Defaults to 1.
            reoccur_days (List[ReoccurDays], optional): When reoccurrence is weekly, on which days to occur.
                                                        Defaults to None.
            reoccur_until (Union[datetime, int], optional): When to stop reoccurring (either date or repeat numbers).
                                                            Defaults to None.
            timezone_override (str, optional): Override the timezone of the event.
                                               Defaults to None (no override)

        Raises:
            ValueError: If trying to use any of the arguments with incompatible modes.
        """
        reoccur_settings = []
        if reoccur_type:
            reoccur_settings.append(f"FREQ={reoccur_type.value}")

        if reoccur_interval:
            reoccur_settings.append(f"INTERVAL={reoccur_interval}")

        if reoccur_days:
            if not reoccur_type == self.ReoccurrenceTypes.WEEKLY:
                raise ValueError('Reoccurrence Days are meaningless if Reoccurrence Type is "WEEKLY"!')

            reoccur_settings.append("BYDAY=" + ",".join([x.value for x in reoccur_days]))

        reoccur_until_str = ""
        if isinstance(reoccur_until, datetime):
            reoccur_until_str = reoccur_until.strftime("%Y%m%d")
        elif isinstance(reoccur_until, int):
            reoccur_settings.append(f"COUNT={reoccur_until}")

        data = {
            **self._event_template,
            "eventType": event_type,
            "person": person,
            "description": description,
            "what": title,
            "startDate": start_date.strftime(self.DATE_FORMAT),
            "startTime": "" if all_day_event else start_date.strftime(self.TIME_FORMAT),
            "endDate": end_date.strftime(self.DATE_FORMAT),
            "endTime": "" if all_day_event else end_date.strftime(self.TIME_FORMAT),
            "allDayEvent": str(all_day_event).lower(),
            "rruleStr": ";".join(reoccur_settings) if reoccur_settings else "",
            "until": reoccur_until_str,
            "userTimeZoneId": timezone_override or self._event_template["userTimeZoneId"],
        }

        self.wiki_instance.put("calendar-services/1.0/calendar/events.json", data=data)
