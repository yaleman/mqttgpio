#!/usr/bin/env python3

"""

MQTT GPIO Home-Assistant controller

Discovery docs: https://www.home-assistant.io/docs/mqtt/discovery/
Switch docs: https://developers.home-assistant.io/docs/en/entity_switch.html

"""
import logging
from os import uname
import time
import sys
import configparser

try:
    import gpiozero # type: ignore
    import paho.mqtt.client as mqtt # type: ignore
    import schedule # type: ignore
except ImportError as import_error:
    sys.exit(f"Package import failure: {import_error}")

from . import GPIOSwitch


LOG_OBJECT = logging.getLogger('mqttcontroller') #pylint: disable: invalid-name
LOG_OBJECT.addHandler(logging.StreamHandler())



CONFIG = configparser.ConfigParser()
CONFIGFILES = ['/etc/mqttgpio.conf', './mqttgpio.conf', '/opt/mqttgpio/mqttgpio.conf']
PARSED_FILES = CONFIG.read(CONFIGFILES)

LOG_LEVEL = CONFIG.get('Default', 'logging', fallback='info')

LOG_LEVELS = {'info' : logging.INFO,
              'debug' : logging.DEBUG,
              'warning' : logging.WARNING,
              'error' : logging.ERROR
             }
if LOG_LEVEL in LOG_LEVELS:
    LOG_OBJECT.setLevel(LOG_LEVELS[LOG_LEVEL])
else:
    LOG_OBJECT.setLevel(logging.DEBUG)
    LOG_OBJECT.debug("Configuration file had a misconfigured 'logging' setting (%s) - setting to DEBUG", LOG_LEVEL) #pylint: disable=line-too-long

LOG_OBJECT.info("Loaded configuration from: %s", ','.join(PARSED_FILES))



def mqtt_on_connect(client_object, userdata, flags, result_code): # noqa: pylint: disable=unused-argument
    """The callback for when the client receives a CONNACK response from the server."""
    LOG_OBJECT.info("Connected with result code %s", result_code)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client_object.subscribe("$SYS/#")
    for device_object in ACTIVE_DEVICES:
        client_object.subscribe(device_object.command_topic())

def mqtt_on_message(client_object, userdata, msg): # noqa: pylint: disable=unused-argument
    """The callback for when a PUBLISH message is received from the server."""
    matched = False
    for device_object in ACTIVE_DEVICES:
        if msg.topic == device_object.command_topic():
            LOG_OBJECT.info("Command to %s : %s", device_object.name, msg.payload)
            device_object.handle_command(msg.payload)
            matched = True
    if not matched and not msg.topic.startswith('$SYS'):
        LOG_OBJECT.info("Command for unknown device: %s=%s", msg.topic, msg.payload)

if __name__ == '__main__':

    MQTT_QOS = CONFIG.getint("MQTT", 'MQTTQOS', fallback=2)
    MQTT_BROKER = CONFIG.get("MQTT", "MQTTBroker", fallback='localhost')
    MQTT_PORT = CONFIG.getint("MQTT", "MQTTPort", fallback=1883)

    MQTTCLIENT = mqtt.Client()
    # callback functions for MQTT
    MQTTCLIENT.on_connect = mqtt_on_connect
    MQTTCLIENT.on_message = mqtt_on_message


    LOG_OBJECT.debug("Connecting to mqtt://%s:%s", MQTT_BROKER, MQTT_PORT)
    while True:
        try:
            MQTTCLIENT.connect(MQTT_BROKER, MQTT_PORT, 60)
            break # break out of the "retry until it works" loop
        except ConnectionRefusedError:
            LOG_OBJECT.info("Unable to connect to mqtt://%s:%s, connection refused. Sleeping for 60 seconds.", # pylint: disable=line-too-long
                            MQTT_BROKER,
                            MQTT_PORT,
                            )
            time.sleep(60)
    LOG_OBJECT.debug("Connected to mqtt://%s:%s", MQTT_BROKER, MQTT_PORT)


    if 'arm' in uname().machine:
        MOCK_PINS = False
    else:
        from gpiozero.pins.mock import MockFactory # type: ignore
        gpiozero.Device.pin_factory = MockFactory()
        MOCK_PINS = True
        LOG_OBJECT.info("Not running on a Pi, using mock objects")


    # create the device/pin associations from the config file
    ACTIVE_DEVICES = []
    if CONFIG.has_section('Devices'):
        for device_name, device_pin in CONFIG.items('Devices'):
            if not device_name.endswith("_default"):
                # look for a device_state option
                config_state = CONFIG.getboolean('Devices',
                                                 f"{device_name}_default",
                                                 fallback=False,
                                                )
                LOG_OBJECT.debug("Creating %s:%s (%s)", device_name, device_pin, config_state)
                try:
                    int_device_pin = int(device_pin)
                except ValueError as error:
                    LOG_OBJECT.error("ValueError handling the configured device pin, bailing: %s", error)
                    sys.exit(1)
                ACTIVE_DEVICES.append(GPIOSwitch(name=device_name,
                                                 pin=int(device_pin),
                                                 client=MQTTCLIENT,
                                                 qos=MQTT_QOS,
                                                 initial_state=config_state,
                                                 mock_pins=MOCK_PINS,
                                                 logging_object=LOG_OBJECT,
                                                 ))

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
        # pylint: disable=broad-except
        except Exception as error_message:
            LOG_OBJECT.error("Failed to do something, sleeping for 60 seconds: %s", error_message)
            time.sleep(60)
