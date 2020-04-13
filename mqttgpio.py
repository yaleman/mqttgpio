#!/usr/bin/env python3

"""

MQTT GPIO Home-Assistant controller

Discovery docs: https://www.home-assistant.io/docs/mqtt/discovery/
Switch docs: https://developers.home-assistant.io/docs/en/entity_switch.html

"""
import json
import logging
from os import uname
import time
import sys
import configparser

try:
    import gpiozero
    import paho.mqtt.client as mqtt
    import schedule
except ImportError as import_error:
    sys.exit(f"Package import failure: {import_error}")


LOG_OBJECT = logging.getLogger('mqttcontroller') #pylint: disable: invalid-name
LOG_OBJECT.addHandler(logging.StreamHandler())
#if sys.platform == 'linux':
if 'arm' in uname().machine:
    MOCK_PINS = False
else:
    from gpiozero.pins.mock import MockFactory
    gpiozero.Device.pin_factory = MockFactory()
    MOCK_PINS = True
    LOG_OBJECT.info("Not running on a Pi, using mock objects")



CONFIG = configparser.ConfigParser()
CONFIGFILES = ['/etc/mqttgpio.conf', './mqttgpio.conf', '/opt/mqttgpio/mqttgpio.conf']
PARSED_FILES = CONFIG.read(CONFIGFILES)

LOG_LEVEL = CONFIG.get('Default', 'logging', fallback='info')

LOG_LEVELS = {'info' : logging.INFO,
              'debug' : logging.DEBUG,
              'warning' : logging.WARNING,
              'error' : logging.ERROR
             }
if LOG_LEVEL in LOG_LEVELS.keys():
    LOG_OBJECT.setLevel(LOG_LEVELS[LOG_LEVEL])
else:
    LOG_OBJECT.setLevel(logging.DEBUG)
    LOG_OBJECT.debug("Configuration file had a misconfigured 'logging' setting (%s) - setting to DEBUG", LOG_LEVEL) #pylint: disable=line-too-long

LOG_OBJECT.info("Loaded configuration from: %s", ','.join(PARSED_FILES))



class GPIOSwitch(): #pylint: disable=too-many-instance-attributes
    """ a single pin controller """
    def __init__(self, name: str,
                 pin: int,
                 client: mqtt.Client,
                 qos: int,
                 logging_object: logging.getLogger = LOG_OBJECT,
                 initial_state: bool = False,
                ): #pylint: disable=too-many-arguments
        self.name = name
        self.device_class = 'switch'
        self.client = client
        self.mqtt_qos = qos
        self.logger = logging_object
        if MOCK_PINS:
            self.pin_io = gpiozero.Device.pin_factory.pin(pin)
        else:
            self.pin_io = gpiozero.LED(pin) # pylint: disable=undefined-variable

        # might as well say hello on startup
        self.announce_config()
        self._set_state(initial_state)

    def str_state(self):
        """ returns the state in the home assistant version """
        if self.state:
            return "ON"
        return "OFF"

    def config_topic(self):
        """ returns the config topic """
        return f"homeassistant/{self.device_class}/{self.name}/config"

    def state_topic(self):
        """ returns the state topic as a string """
        return f"{self.name}/state"

    def command_topic(self):
        """ returns the command topic as a string """
        return f"{self.name}/cmnd"

    def _publish(self, topic, payload):
        """ publishes a message """
        return self.client.publish(topic,
                                   payload,
                                   qos=self.mqtt_qos,
                                   )


    def announce_config(self):
        """ sends the MQTT message to configure
            home assistant
        """
        payload = {
            'name' : self.name,
            'state_topic' : self.state_topic(),
            'command_topic' : self.command_topic(),
            "val_tpl" : '{{value_json.POWER}}',
        }
        self.logger.debug(
            "%s.announce_config(%s)",
            self.name,
            payload,
            )
        self._publish(
            self.config_topic(),
            payload=json.dumps(payload),
            )

    def announce_state(self):
        """ sends the MQTT message about the current state """
        payload = {'POWER' : self.str_state()}

        self.logger.debug("%s.announce_state(%s)", self.name, payload)
        self._publish(self.state_topic(), payload=json.dumps(payload))

    def _set_state(self, state):
        """ Does a few things:
            - sets the internal state variable
            - sets the GPIO
            - announces via MQTT the current state
        """
        if MOCK_PINS:
            if state:
                self.pin_io.drive_low()
            else:
                self.pin_io.drive_high()
            self.logger.debug("%s:%s (dev-mode) = %s", self.name, self.pin_io, state)
        else:
            if state:
                self.pin_io.on()
            else:
                self.pin_io.off()
            self.logger.debug("%s:%s (GPIO) = %s", self.name, self.pin_io, state)
        self.state = state
        self.announce_state()

    def handle_command(self, payload):
        """ takes actions based on incoming commands """
        self.logger.debug("%s.handle_command(%s)", self.name, payload)
        if payload == b'ON':
            self._set_state(True)
        elif payload == b'OFF':
            self._set_state(False)
        else:
            LOG_OBJECT.WARN("%s.handle_command(%s) is weird - should match '(ON|OFF)'", self.name, payload) # pylint: disable=line-too-long

def on_connect(client_object, userdata, flags, result_code): # noqa: pylint: disable=unused-argument
    """The callback for when the client receives a CONNACK response from the server."""
    LOG_OBJECT.info("Connected with result code %s", result_code)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client_object.subscribe("$SYS/#")
    for device_object in ACTIVE_DEVICES:
        client_object.subscribe(device_object.command_topic())

def on_message(client_object, userdata, msg): # noqa: pylint: disable=unused-argument
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
    MQTTCLIENT.on_connect = on_connect
    MQTTCLIENT.on_message = on_message


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
                ACTIVE_DEVICES.append(GPIOSwitch(name=device_name,
                                                 pin=device_pin,
                                                 client=MQTTCLIENT,
                                                 qos=MQTT_QOS,
                                                 initial_state=config_state,
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
        schedule.run_pending()
        time.sleep(1)
