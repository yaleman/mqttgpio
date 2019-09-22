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
logger.addHandler(logging.StreamHandler())


cfg = configparser.ConfigParser()
configfiles = ['/etc/mqttgpio.conf', './mqttgpio.conf', '/opt/mqttgpio/mqttgpio.conf']
parsed_files = cfg.read(configfiles)

log_level = cfg.get('Default', 'logging', fallback='info')

log_levels = { 'info' : logging.INFO, 'debug' : logging.DEBUG, 'warning' : logging.WARNING, 'error' : logging.ERROR }
if log_level in log_levels.keys():
    logger.setLevel(log_levels[log_level])
else:
    logger.setLevel(logging.DEBUG)
    logger.debug(f"Configuration file had a misconfigured 'logging' setting ({log_level}) - setting to DEBUG")

logger.info(f"Loaded configuration from: {','.join(parsed_files)}")

mqtt_qos = cfg.getint("MQTT", 'MQTTQOS', fallback=2)
mqtt_broker = cfg.get("MQTT", "MQTTBroker", fallback='localhost')
mqtt_port = cfg.getint("MQTT", "MQTTPort", fallback=1883)

client = mqtt.Client()


class GPIOSwitch(object):
    def __init__(self, name : str, pin : int, client : mqtt.Client=client, state : bool=False, logger : logging.getLogger=logger, mqtt_qos : int=mqtt_qos):
        self.name = name
        self.device_class = 'switch'
        self.mqtt_qos = mqtt_qos
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

    def _publish(self, topic, payload):
        """ publishes a message """
        return self.client.publish(topic, payload, qos=self.mqtt_qos)


    def announce_config(self):
        """ sends the MQTT message to configure home assistant """
        payload = {
            'name' : self.name,
            'state_topic' : self.state_topic(),
            'command_topic' : self.command_topic(),
            "val_tpl" : '{{value_json.POWER}}',
        }
        self.logger.debug(f"{self.name}.announce_config({str(payload)})")
        self._publish(self.config_topic(), payload=json.dumps(payload))

    def announce_state(self):
        """ sends the MQTT message about the current state """
        payload = { 'POWER' : self.str_state() }
        
        self.logger.debug(f"{self.name}.announce_state({str(payload)})")
        self._publish(self.state_topic(), payload=json.dumps(payload))

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

# callback functions for MQTT
client.on_connect = on_connect
client.on_message = on_message


logger.debug(f"Connecting to mqtt://{mqtt_broker}:{mqtt_port}")
while True:
    try:
        client.connect(mqtt_broker, mqtt_port, 60)
        break # break out of the "retry until it works" loop
    except ConnectionRefusedError:
        logger.info(f"Unable to connect to mqtt://{mqtt_broker}:{mqtt_port}, connection refused. Sleeping for 60 seconds.")
        time.sleep(60)
logger.debug(f"Connected to mqtt://{mqtt_broker}:{mqtt_port}")

# create the device/pin associations from the config file
devices = []
if cfg.has_section('Devices'):
    for name, pin in cfg.items('Devices'):
        if name.endswith("_default") == False:
            # look for a device_state option
            state = cfg.getboolean('Devices', f"{name}_default", fallback=False)
            logger.debug(f"Creating {name}:{pin} ({state})")
            devices.append(GPIOSwitch(name=name, pin=pin, state=state))

logger.debug("Starting the MQTT thread")
client.loop_start()

logger.debug("Scheduling regular events... ")
for device in devices:
    schedule.every(5).minutes.do(device.announce_config)
    schedule.every(30).seconds.do(device.announce_state)
logger.debug("Scheduling complete.")

logger.info("Starting the main loop")
while True:
    schedule.run_pending()
    time.sleep(1)
