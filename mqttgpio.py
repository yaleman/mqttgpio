#!/usr/bin/env python3

"""

MQTT GPIO Home-Assistant controller

Discovery docs: https://www.home-assistant.io/docs/mqtt/discovery/
Switch docs: https://developers.home-assistant.io/docs/en/entity_switch.html

"""
import json
import logging
import time
import sys
import configparser
import os

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("You need to install paho-mqtt - pip3 install paho-mqtt")
try:
    import schedule
except ImportError:
    sys.exit("You need to install schedule - pip3 install schedule")

if sys.platform == 'linux':
    # hopefully the raspi
    try:
        import gpiozero
    except ImportError:
        sys.exit("You need to install gpiozero")
    DO_PINS = True
else:
    DO_PINS = False 

logger = logging.getLogger('mqttcontroller')
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())


cfg = configparser.ConfigParser()
configfiles = ['/etc/mqttgpio.conf', './mqttgpio.conf', '/opt/mqttgpio/mqttgpio.conf']
cfg.read(configfiles)
MQTT_QOS = cfg.getint("MQTT", 'MQTTQOS', fallback=2)

client = mqtt.Client()


class GPIOSwitch(object):
    def __init__(self, name : str, pin : int, client : mqtt.Client=client, state : bool=False, logger : logging.getLogger=logger):
        self.name = name
        self.device_class = 'switch'
        self.client = client
        self.logger = logger
        self.pin = pin
        if DO_PINS:
            self.pin_io = gpiozero.LED(self.pin)

        # might as well say hello on startup
        self.announce_config()
        self._set_state(state)
    
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

    def announce_config(self):
        """ sends the MQTT message to configure home assistant """
        payload = {
            'name' : self.name,
            'state_topic' : self.state_topic(),
            'command_topic' : self.command_topic(),
            "val_tpl" : '{{value_json.POWER}}',
        }
        self.logger.debug(f"{self.name}.announce_config({str(payload)})")
        client.publish(self.config_topic(), payload=json.dumps(payload), qos=MQTT_QOS)
        return

    def announce_state(self):
        """ sends the MQTT message about the current state """
        payload = { 'POWER' : self.str_state() }
        
        self.logger.debug(f"{self.name}.announce_state({str(payload)})")
        client.publish(self.state_topic(), payload=json.dumps(payload), qos=MQTT_QOS)

    def _set_state(self, state):
        """ Does a few things:
            - sets the internal state variable
            - sets the GPIO
            - announces via MQTT the current state
        """
        if DO_PINS:
            if state:
                self.pin_io.on()
            else:
                self.pin_io.off()
            self.logger.debug(f"{self.name}:{pin} (GPIO) = {state}")
        else:
            self.logger.debug(f"{self.name}:{pin} (dev-mode) = {state}")
        self.state = state
        self.announce_state()

    def handle_command(self, payload):
        """ takes actions based on incoming commands """
        self.logger.debug(f"{self.name}.handle_command({payload})")
        if payload == b'ON':
            self._set_state(True)
        elif payload == b'OFF':
            self._set_state(False)
        else:
            logger.WARN(f"{self.name}.handle_command({payload}) is weird - should match '(ON|OFF)'")

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logger.info(f"Connected with result code {rc}")

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("$SYS/#")
    for device in devices:
        client.subscribe(device.command_topic())

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    matched = False
    for device in devices:
        if msg.topic == device.command_topic():
            logger.info(f"Command to {device.name}: {str(msg.payload)}")
            device.handle_command(msg.payload)
            matched = True
    if not matched and msg.topic.startswith('$SYS') == False:
        logger.info(f"Command for unknown device: {msg.topic}={str(msg.payload)}")


# set up a scheduler to allow regular messages


client.on_connect = on_connect
client.on_message = on_message


logger.debug("Connecting...")
client.connect(cfg.get("MQTT", "MQTTBroker", fallback='localhost'), cfg.getint("MQTT", "MQTTPort", fallback=1883), 60)
logger.debug("Done!")
# start a non-blocking client loop in a thread

devices = []
if cfg.has_section('Devices'):
    for name, pin in cfg.items('Devices'):
        if name.endswith("_default") == False:
            # make a device
            # look for a device_state option
            state = cfg.getboolean('Devices', f"{name}_default", fallback=False)
            logger.debug(f"Creating {name}:{pin} ({state})")
            devices.append(GPIOSwitch(name=name, pin=pin, state=state))

logger.debug("Loop time.")
client.loop_start()

logger.debug("Scheduling regular events...")
for device in devices:
    schedule.every(5).minutes.do(device.announce_config)
    schedule.every(30).seconds.do(device.announce_state)
logger.debug("Done")

logger.info("Starting the main loop")
while True:
    schedule.run_pending()
    time.sleep(1)

logger.debug("Loop stopping.")
client.loop_stop()
