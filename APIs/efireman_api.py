"""
efireman_api
"""

import logging
import random
import re
import time
import json
import requests

log = logging.getLogger("EfiremanApi")


class EfiremanApi:
    """
    The E-Firemane instance.
        (should) contain all REST API requests from eFireman framework.

    General: for each api request there should be a privat method constructing the URL,
             e.g. for analyzing the WHCK logs there is the __build_efireman_analyzer_url
    """

    def __init__(self, server_name="efireman.intel.com/"):
        """
        construnct the eFireman object
        """
        self.server_name = server_name

    def trigger_efireman_whql_analyzer(self, network_link, analyzer_type, output_type):
        """
        REST API request to the eFireman whql analyzer.

        Args:
            network_link (str) - the path to the log file.
            analyzer_type (str)   - the type of pareser the eFireman uses (correlates with the type of log).
            output_type (str)     - provide "json" to get responce in json formant otherewise it will be in html.
        return:
            (str): the response test or None in case of HTTP excption.
        """
        log.setLevel(logging.INFO)
        input_type = "network_link"  # the type of method used to upload the log file.
        is_e2go = 0  # provide 0 to disable the feature or 1 to enable it.
        report_name = "wifi_driver_test_analysis" + str(random.randint(1, 100))

        # URL and data for post request
        post_url = "http://efireman.intel.com"
        data = {
            "input_type": input_type,
            "analyzer_type": analyzer_type,
            "report_name": report_name,
            "network_link": network_link,
            "is_e2go": is_e2go,
            "output_type": output_type,
        }

        log.info("Calling eFireman with the data: %s", json.dumps(data, indent=4))
        response = requests.post(post_url, data=data, timeout=5)
        response.raise_for_status()

        # Getting the report ID from the HTML
        report_id = re.search(r"Analyzing Report (\d+)", response.text).group(1)
        retries_counter = 0
        retries_max = 5

        if report_id:
            time.sleep(5)
            while "<!DOCTYPE html>" in response.text:
                get_url = f"{post_url}/analyzer_reports/{report_id}"
                response = requests.get(get_url, data={}, timeout=5)
                response.raise_for_status()
                time.sleep(5)
                retries_counter += 1
                if retries_counter >= retries_max:
                    raise ConnectionError("Couldn't get the json report after the retries!")

        return response.text
