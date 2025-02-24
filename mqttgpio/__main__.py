#!/usr/bin/env python3

"""

MQTT GPIO Home-Assistant controller

Discovery docs: https://www.home-assistant.io/docs/mqtt/discovery/
Switch docs: https://developers.home-assistant.io/docs/en/entity_switch.html

"""

import logging
from os import uname
import socket
import time
import sys
from typing import Any

try:
    import gpiozero  # type: ignore
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion
    from paho.mqtt.client import Client, MQTTMessage
    from paho.mqtt.reasoncodes import ReasonCode
    import schedule
except ImportError as import_error:
    sys.exit(f"Package import failure: {import_error}")

from mqttgpio import GPIOSwitch, load_config

LOG_OBJECT = logging.getLogger("mqttcontroller")  # pylint: disable: invalid-name
LOG_OBJECT.addHandler(logging.StreamHandler())

CONFIG = load_config(LOG_OBJECT)


class FailedToConnect(BaseException):
    """Raised when the MQTT connection fails"""

    def __init__(self, reason_code: ReasonCode) -> None:
        super().__init__(f"Failed to connect to MQTT, reason code: {reason_code}")


def mqtt_on_connect(
    client_object: Client,
    _userdata: Any,
    _flags: dict[str, Any],
    reason_code: ReasonCode,
    _properties: dict[str, Any],
) -> None:
    """The callback for when the client receives a CONNACK response from the server."""

    if reason_code.getId(str(reason_code)) > 0:  # type: ignore[no-untyped-call]
        LOG_OBJECT.error(
            "Connection failed, reason code %s (%d)",
            reason_code,
            reason_code.getId(str(reason_code)),  # type: ignore[no-untyped-call]
        )
        raise FailedToConnect(reason_code)
    else:
        LOG_OBJECT.info(
            "Connected with reason code %s (%d)",
            reason_code,
            reason_code.getId(str(reason_code)),  # type: ignore[no-untyped-call]
        )

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client_object.subscribe("$SYS/#")
    for device_object in ACTIVE_DEVICES:
        client_object.subscribe(device_object.command_topic())


def mqtt_on_message(_client_object: Client, _userdata: Any, msg: MQTTMessage) -> None:
    """The callback for when a PUBLISH message is received from the server."""
    matched = False
    for device_object in ACTIVE_DEVICES:
        if msg.topic == device_object.command_topic():
            LOG_OBJECT.info("Command to %s : %s", device_object.name, msg.payload)
            device_object.handle_command(msg.payload)
            matched = True
    if not matched and not msg.topic.startswith("$SYS"):
        LOG_OBJECT.info("Command for unknown device: %s=%s", msg.topic, msg.payload)


if __name__ == "__main__":
    MQTT_QOS = CONFIG.getint("MQTT", "MQTTQOS", fallback=2)
    MQTT_BROKER = CONFIG.get("MQTT", "MQTTBroker", fallback="localhost")
    MQTT_PORT = CONFIG.getint("MQTT", "MQTTPort", fallback=1883)

    MQTTCLIENT = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    # callback functions for MQTT - type ignore is because of how the API is defined, V2 is different
    MQTTCLIENT.on_connect = mqtt_on_connect  # type: ignore[assignment]
    MQTTCLIENT.on_message = mqtt_on_message

    LOG_OBJECT.debug("Connecting to mqtt://%s:%s", MQTT_BROKER, MQTT_PORT)
    while True:
        try:
            MQTTCLIENT.connect(MQTT_BROKER, MQTT_PORT, 60)
            break  # break out of the "retry until it works" loop
        except socket.gaierror as error:
            LOG_OBJECT.error(
                "Failed to resolve the MQTT broker hostname, sleeping for 60 seconds: %s",
                error,
            )
            time.sleep(60)
        except ConnectionRefusedError:
            LOG_OBJECT.info(
                "Unable to connect to mqtt://%s:%s, connection refused. Sleeping for 60 seconds.",  # pylint: disable=line-too-long
                MQTT_BROKER,
                MQTT_PORT,
            )
            time.sleep(60)
    LOG_OBJECT.debug("Connected to mqtt://%s:%s", MQTT_BROKER, MQTT_PORT)

    if "arm" in uname().machine:
        MOCK_PINS = False
    else:
        from gpiozero.pins.mock import MockFactory  # type: ignore

        gpiozero.Device.pin_factory = MockFactory()
        MOCK_PINS = True
        LOG_OBJECT.info("Not running on a Pi, using mock objects")

    # create the device/pin associations from the config file
    ACTIVE_DEVICES: list[GPIOSwitch] = []
    if CONFIG.has_section("Devices"):
        for device_name, device_pin in CONFIG.items("Devices"):
            if not device_name.endswith("_default"):
                # look for a device_state option
                config_state = CONFIG.getboolean(
                    "Devices",
                    f"{device_name}_default",
                    fallback=False,
                )
                LOG_OBJECT.debug(
                    "Creating %s:%s (%s)", device_name, device_pin, config_state
                )
                try:
                    int_device_pin = int(device_pin)
                except ValueError as error:
                    LOG_OBJECT.error(
                        "ValueError handling the configured device pin, bailing: %s",
                        error,
                    )
                    sys.exit(1)
                ACTIVE_DEVICES.append(
                    GPIOSwitch(
                        name=device_name,
                        pin=int(device_pin),
                        client=MQTTCLIENT,
                        qos=MQTT_QOS,
                        initial_state=config_state,
                        mock_pins=MOCK_PINS,
                        logging_object=LOG_OBJECT,
                    )
                )

    LOG_OBJECT.debug("Starting the MQTT thread")
    MQTTCLIENT.loop_start()

    LOG_OBJECT.debug("Scheduling regular events... ")
    for device in ACTIVE_DEVICES:
        schedule.every(5).minutes.do(device.announce_config)
        schedule.every(30).seconds.do(device.announce_state)
    LOG_OBJECT.debug("Scheduling complete.")

    LOG_OBJECT.info("Starting the main loop")
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        # a hail-mary to keep it running :)
        except KeyboardInterrupt:
            LOG_OBJECT.info("Exiting due to user input")
            sys.exit(0)
        except Exception as error_message:
            LOG_OBJECT.error(
                "Failed to do something, sleeping for 60 seconds: %s", error_message
            )
            time.sleep(60)
