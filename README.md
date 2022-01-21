[![Python checks](https://github.com/vladak/weather/actions/workflows/python-checks.yml/badge.svg)](https://github.com/vladak/weather/actions/workflows/python-checks.yml)

# Weather

Simple weather monitoring Python script. Collects these metrics:

| *Metric* | *Sensor* | *Sensor access* |
| ------------- |:-------------:| :-------------: |
| [temperature](https://en.wikipedia.org/wiki/Temperature) | 1-wire | [OWFS](https://www.owfs.org/) |
| [barometric pressure](https://en.wikipedia.org/wiki/Atmospheric_pressure) | [BMP280](https://www.adafruit.com/product/2651) | [I2C](https://en.wikipedia.org/wiki/I%C2%B2C) |
| [CO2](https://en.wikipedia.org/wiki/Carbon_dioxide) and [humidity](https://en.wikipedia.org/wiki/Humidity) | [SCD-40](https://www.adafruit.com/product/5187) | [I2C](https://en.wikipedia.org/wiki/I%C2%B2C) |
| [air particles](https://en.wikipedia.org/wiki/Particulates) | [PMSA003I](https://www.adafruit.com/product/4632) | [I2C](https://en.wikipedia.org/wiki/I%C2%B2C) |

The I2C sensors are connected to the [Raspberry Pi](https://www.raspberrypi.org/) via the [SparkFun Qwiic / STEMMA QT HAT](https://www.adafruit.com/product/4688) that offers 4 [Stemma Qt](https://learn.adafruit.com/introducing-adafruit-stemma-qt/what-is-stemma-qt) ports.

## Setup

### I2C

- enable I2C via `sudo raspi-config`
  - it is under `Interface Options`
- verify I2C bus presence via `sudo i2cdetect -l`
  - should report something like this: `i2c-1	i2c       	bcm2835 (i2c@7e804000)          	I2C adapter`

### OWFS

- needs OWFS system package:
```
  sudo apt-get -y install owfs
```
- change `/etc/owfs.conf` to contain the following line and comment about any
  lines with `FAKE` sensors
```
server: usb = all
```

Initially this was not working and the `owfs` service complained about no bus
being seen. `apt-get update && apt-get upgrade` pulled bunch of raspberrypi
kernel updates and after reboot the sensors were available under the `/run/owfs`
directory.


## Install the weather service

This is a simple Python program that collects the metrics and presents them via 
[Prometheus web server](https://github.com/prometheus/client_python) so they
can be scraped by Prometheus.

- clone the repository to `/srv/weather/`:
```
  git clone https://github.com/vladak/weather.git /srv/weather
```
- install requirements
```
  cd /srv/weather
  python3 -m venv env
  . ./env/bin/activate
  pip install -r requirements.txt
```
- add the `weather` service
```
  sudo cp weather.service /etc/systemd/system/
  sudo systemctl enable weather
  # if the file /etc/systemd/system/weather.service changes, run:
  sudo systemctl daemon-reload
  # start the service:
  sudo systemctl start weather
  sudo systemctl status weather
```

## Prometheus

TBD

## Grafana

- install Grafana (standalone)
- provision the dashboards from the `.json` files
- setup Alert notification channels (set both as _Default_):
  - [PagerDuty](https://www.pagerduty.com/)
  - `localhost:8333` (for the _Alert handler_ below)

The Weather dashboard looks like this:

![Weather dashboard](/img/grafana-weather.jpg)

In the graphs one can see the CO2 metric above the threshold. This generated an alert.

### Alert handler

The goal is to play a sound when Grafana produces an alert that matches certain criteria,
e.g. when the CO2 metric rises above given level (time to open a window).

- connect the [USB speaker](https://www.adafruit.com/product/3369)
- install pre-requisites:
```
  sudo apt-get install -y mpg123
```
- setup sound card in Alsa config:
```
  sed -i /usr/share/alsa/alsa.conf \
      -e 's/^defaults.ctl.card 0/defaults.ctl.card 1/' \
      -e 's/^defaults.pcm.card 0/defaults.pcm.card 1/'
```
- copy some MP3 files (with `.mp3` suffix) to `/srv/weather/`
- install the service
```
  sudo cp /srv/weather/alert.service /etc/systemd/system/
  sudo systemctl enable alert
  sudo systemctl daemon-reload
  sudo systemctl start alert
  sudo systemctl status alert
```
- test the alert in Grafana (it should start playing the MP3 file)
  - go to Alerts -> Notification channels and hit 'Test'

## Links

### Guides

- SCD-40 guide: https://learn.adafruit.com/adafruit-scd-40-and-scd-41/python-circuitpython
- PMSA003I guide: https://github.com/adafruit/Adafruit_CircuitPython_PM25
- USB card with Raspberry Pi: https://learn.adafruit.com/usb-audio-cards-with-a-raspberry-pi/updating-alsa-config
