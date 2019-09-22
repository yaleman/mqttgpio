# mqttgpio

[Home Assistant](https://home-assistant.io) autoconfiguring [MQTT](http://www.mqtt.org/)-powered GPIO control for a Raspberry Pi.

This is designed to allow you to automagically configure some [switches](https://developers.home-assistant.io/docs/en/entity_switch.html) to show up in Home Assitant which control GPIOs on your Raspberry Pi. This was made because I couldn't get the Home Assistant remote GPIO functionality to work when running HA in [docker](https://docker.com/).

It also avoids having to allow remote access to the pigpio daemon, which is kinda bad.

## Configuration

The configuration file needs to be in one of the following locations:

* `/etc/mqttgpio.conf`
* `./mqttgpio.conf` (local to where you're running it)
* `/opt/mqttgpio/mqttgpio.conf`

See `mqttgpio.conf.example` for an example of how to configure the application.

## Requirements

The following python libraries:

* paho.mqtt.client
* schedule
* gpiozero

You'll need to be running pigpio, which is fairly easy to install and start:

`sudo apt install pigpio pigpio-tools pigpiod; sudo systemctl enable pigpiod; sudo systemctl start pigpiod`

You might need to change the configuration in `raspi-config` depending on what you've been playing with.

## Installation

1. `sudo git clone https://github.com/yaleman/mqttgpio /opt/mqttgpio`
2. `cd /opt/mqttgpio`
3. `sudo ln -s /opt/mqttgpio/mqttgpio.service /etc/systemd/system/mqttgpio.service`
4. `sudo pip3 install -r /opt/mqttgpio/requirements.txt`
5. `sudo systemctl enable mqttgpio`
6. Make sure you configure your `mqttgpio.conf` file in one of the above locations.
7. `sudo systemctl start mqttgpio`

This'll run as root, if you want to change the user it runs as, undo the link, copy mqttgpio.service to `/etc/systemd/system/` and add a `User=` line to the Service section. Documentation for the service file format is [here on freedesktop.org](https://www.freedesktop.org/software/systemd/man/systemd.service.html).

## Updating

In the case that I update my code, or you do:

1. `cd /opt/mqttgpio`
2. `git pull`
3. `sudo systemctl daemon-reload` (if the `.service` file has changed, or you've edited it)
4. `sudo systemctl restart mqttgpio`

## Troubleshooting

*I'm seeing "Unable to connect to MQTT Broker localhost:1883" but I thought I configured it properly*

Double check the configuration file, you might have mistyped something, or you might have multiple config files. You'll probably see a line like "Configuration file had a misconfigured 'logging' setting (something) - setting to DEBUG" if it doesn't match the options available.

## TODO

* Maybe make it so it reads back the GPIO state periodically, and if it's changed (ie, something else changes it) then udpate the state