"""
An API for using the PDU
Includes: power on, power off, power cycle and connection status.
"""

import logging
from time import sleep
from datetime import datetime
from raritan.rpc import pdumodel
from raritan import rpc
from raritan.rpc.pdumodel import Outlet
from Sherlock import Database
from DatabaseAPI import DBConnector
from utils import ping

log = logging.getLogger("PduApi")


class PDU:
    """
    Creates a PDU agent.
    """

    def __init__(self, ip_address, username, passwd):
        log.info("Performs a PDU agent initialization")
        self.ip_address = ip_address
        self.db = DBConnector(Database.server, Database.database, Database.username, Database.password)

        self.agent = rpc.Agent(proto="https", host=ip_address, user=username, passwd=passwd)

    @staticmethod
    def _wait_for_machine(host, timeout=300):
        start_time = datetime.now()
        while True:
            if (datetime.now() - start_time).seconds > timeout:
                raise TimeoutError(f"The host {host} did not come back within the alloted {timeout} seconds!")
            ping_result = ping(host=host, packets=1)
            if ping_result:
                return

    def power_on(self, outlet):
        """
        Powers on the device.
        Args:
            outlet (int): The outlet number as defined in the PDU GUI.
        """
        log.info("Performs power on, on outlet #%s", outlet)
        pdu = pdumodel.Pdu("/model/pdu/0", self.agent)
        outlets = pdu.getOutlets()
        outlets[outlet - 1].setPowerState(pdumodel.Outlet.PowerState.PS_ON)
        if self.is_power_state_on(outlet) is False:
            raise PDUActionFailed(outlet, "power on")
        log.info("Power on has been done successfully on outlet #%s", outlet)
        # Find the relevant machine by the pdu and outlet in order to check if it's up.
        machine = self.db.get_single_row(
            "pdu.mapping", select="machine,ip_address", where=f"pdu = '{self.ip_address}' AND outlet = '{outlet}'"
        )

        if not machine["ip_address"]:
            hostname = machine["machine"]
            raise ValueError(f"Couldn't find the ip_address in DB for {hostname}")

        PDU._wait_for_machine(machine["ip_address"])

    def power_off(self, outlet):
        """
        Powers off the device.
        Args:
            outlet (int): The outlet number as defined in the PDU GUI.
        """
        log.info("Performs power off, on outlet #%s", outlet)
        pdu = pdumodel.Pdu("/model/pdu/0", self.agent)
        outlets = pdu.getOutlets()
        outlets[outlet - 1].setPowerState(pdumodel.Outlet.PowerState.PS_OFF)
        if self.is_power_state_on(outlet) is True:
            raise PDUActionFailed(outlet, "power off")
        log.info("Power off has been done successfully on outlet #%s", outlet)

    def power_cycle(self, outlet, cycle_time):
        """
        Powers cycle the device.
        Args:
            outlet (int): The outlet number as defined in the PDU GUI.
            cycle_time (int): The amount of time before powering back on (sec).
        """
        log.info("Performs power cycle with cycle time of %s sec, on outlet #%s", cycle_time, outlet)
        self.power_off(outlet)
        sleep(cycle_time)
        self.power_on(outlet)
        log.info("Power cycle has been done successfully on outlet #%s", outlet)

    def is_power_state_on(self, outlet):
        """
        Getting the device's power state.
        Args:
            outlet (int): The outlet number as defined in the PDU GUI.
        Returns:
            boolean: True when the power state is on, False when it's off.
        """
        log.info("Performs a power state check on outlet #%s", outlet)
        pdu = pdumodel.Pdu("/model/pdu/0", self.agent)
        outlets = pdu.getOutlets()
        if outlets[outlet - 1].getState().powerState == Outlet.PowerState.PS_ON:
            log.info("Outlet #%s is ON", outlet)
            return True
        if outlets[outlet - 1].getState().powerState == Outlet.PowerState.PS_OFF:
            log.info("Outlet #%s is OFF", outlet)
            return False
        raise ValueError(f"An unexpected value when getting the power state of outlet #{outlet}")


class PDUActionFailed(Exception):
    """Exception for when validating the PDU action, we see that it failed turning on or off the outlet"""

    def __init__(self, outlet, action):
        self.outlet = outlet
        self.action = action
        super().__init__(f"Failed to {self.action} outlet #{self.outlet}")
