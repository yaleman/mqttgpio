""" mqttgpio - a shim between MQTT and GPIO on raspberry Pi """

import json
import logging

import gpiozero # type: ignore
import paho.mqtt.client as mqtt # type: ignore

class GPIOSwitch(): #pylint: disable=too-many-instance-attributes
    """ a single pin controller """
    def __init__(self, name: str,
                 pin: int,
                 client: mqtt.Client,
                 qos: int,
                 logging_object: logging.Logger,
                 initial_state: bool = False,
                 mock_pins: bool = False,
                ): #pylint: disable=too-many-arguments
        self.name = name
        self.device_class = 'switch'
        self.client = client
        self.mqtt_qos = qos
        self.logger = logging_object
        self.mock_pins = mock_pins
        if mock_pins:
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
        if self.mock_pins:
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
            self.logger.WARN("%s.handle_command(%s) is weird - should match '(ON|OFF)'", self.name, payload) # pylint: disable=line-too-long
