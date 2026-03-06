#!/usr/bin/python
# type: ignore
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 by Christian Tremblay, P.Eng <christian.tremblay@servisys.com>
# Licensed under LGPLv3, see file LICENSE in this source tree.
#
"""
Simulate.py - simulate the value of controller I/O values
"""

from bacpypes3.app import Application

from ..app.asyncApp import BAC0Application
from .IOExceptions import (
    ApplicationNotStarted,
    NoResponseFromController,
    OutOfServiceNotSet,
    OutOfServiceSet,
)

# --- standard Python modules ---
# --- 3rd party modules ---
# --- this application's modules ---
from .Write import WriteProperty

# ------------------------------------------------------------------------------

VENDORS_REQUIRING_RELIABILITY_FOR_OoS = [5]
FORCE_RELIABILITY = True


class Simulation:
    """
    Global informations regarding simulation
    """

    async def sim(self, args, vendor_id: int = 0):
        """
        Simulate I/O points by setting the Out_Of_Service property, then doing a
        WriteProperty to the point's Present_Value.
        In the case of JCI controllers, we will also send a reliability "reliable" value
        to overcome the priority given to "communication error" over out of service.

        :param args: String with <addr> <type> <inst> <prop> <value> [ <indx> ] [ <priority> ]

        """
        if not self._started:
            raise ApplicationNotStarted("BACnet stack not running - use startApp()")
        _this_application: BAC0Application = self.this_application
        _app: Application = _this_application.app
        (
            address,
            obj_type,
            obj_inst,
            prop_id,
            value,
            priority,
            indx,
        ) = WriteProperty._parse_wp_args(args)

        if await self.is_out_of_service(args):
            await self._write(args)
            if vendor_id in VENDORS_REQUIRING_RELIABILITY_FOR_OoS or FORCE_RELIABILITY:
                await self.force_reliability(args)
        else:
            try:
                await self.out_of_service(args)
            except NoResponseFromController as e:
                self.log(
                    f"Failed to write to OutOfService property ({e})", level="warning"
                )

            try:
                if await self.is_out_of_service(args):
                    await self._write(
                        f"{address} {obj_type} {obj_inst} {prop_id} {value}"
                    )
                    if (
                        vendor_id in VENDORS_REQUIRING_RELIABILITY_FOR_OoS
                        or FORCE_RELIABILITY
                    ):
                        await self.force_reliability(args)
                else:
                    raise OutOfServiceNotSet()
            except NoResponseFromController as e:
                self.log(
                    f"Failed to write to OutOfService property ({e})", level="warning"
                )

    async def is_out_of_service(self, args):
        if not self._started:
            raise ApplicationNotStarted("BACnet stack not running - use startApp()")
        _this_application: BAC0Application = self.this_application
        _app: Application = _this_application.app
        (
            address,
            obj_type,
            obj_inst,
            prop_id,
            value,
            priority,
            indx,
        ) = WriteProperty._parse_wp_args(args)

        oos = await self.read(f"{address} {obj_type} {obj_inst} outOfService")

        return True if oos else False

    async def out_of_service(self, args):
        """
        Set the Out_Of_Service property so the Present_Value of an I/O may be written.

        :param args: String with <addr> <type> <inst> <prop> <value> [ <indx> ] [ <priority> ]

        """
        if not self._started:
            raise ApplicationNotStarted("BACnet stack not running - use startApp()")
        (
            address,
            obj_type,
            obj_inst,
            prop_id,
            value,
            priority,
            indx,
        ) = WriteProperty._parse_wp_args(args)
        try:
            await self._write(f"{address} {obj_type} {obj_inst} outOfService True")
        except NoResponseFromController as e:
            self.log(f"Failed to write to OutOfService property ({e})", level="warning")

    async def force_reliability(self, args):
        """
        Set reliability property to NO_FAULT_DETECTED.
        Or else, in some cases, internal condition or previous reliability will prevent
        the value from being used internally.

        :param args: String with <addr> <type> <inst> <prop> <value> [ <indx> ] [ <priority> ]

        """
        if not self._started:
            raise ApplicationNotStarted("BACnet stack not running - use startApp()")
        (
            address,
            obj_type,
            obj_inst,
            prop_id,
            value,
            priority,
            indx,
        ) = WriteProperty._parse_wp_args(args)
        try:
            await self._write(f"{address} {obj_type} {obj_inst} reliability 0")
        except NoResponseFromController as e:
            self.log(f"Failed to write to OutOfService property ({e})", level="warning")

    async def release(self, args):
        """
        Set the Out_Of_Service property to False - to release the I/O point back to
        the controller's control.

        :param args: String with <addr> <type> <inst>

        """
        if not self._started:
            raise ApplicationNotStarted("BACnet stack not running - use startApp()")

        (
            address,
            obj_type,
            obj_inst,
            prop_id,
            value,
            priority,
            indx,
        ) = WriteProperty._parse_wp_args(args)
        try:
            await self._write(f"{address} {obj_type} {obj_inst} outOfService False")
        except NoResponseFromController as e:
            self.log(f"Failed to write to OutOfService property ({e})", level="warning")

        try:
            if await self.is_out_of_service(args) is True:
                raise OutOfServiceSet()
            else:
                pass  # Everything is ok"
        except NoResponseFromController as e:
            self.log(f"Failed to read OutOfService property ({e})", level="warning")
