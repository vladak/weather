[![Python checks](https://github.com/vladak/weather/actions/workflows/python-checks.yml/badge.svg)](https://github.com/vladak/weather/actions/workflows/python-checks.yml)

# Weather

Simple weather monitoring Python script. Collects the temperature data using
OWFS and barometric pressure data using [BMP280](https://www.adafruit.com/product/2651) sensor connected via I2C,
uses Prometheus web server to export the data.

## Setup

### I2C

- enable I2C via `sudo raspi-config`
- verify that the sensor is visible via `sudo i2cdetect -l`
  - should report something like this: `i2c-1	i2c       	bcm2835 (i2c@7e804000)          	I2C adapter`

### OWFS

```
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


## Install

- clone the repository to `/srv/`:
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
- copy `weather.service` file to `/etc/systemd/system/weather.service`:
```
  sudo cp weather.service /etc/systemd/system/weather.service
```
- enable the service:
```
  sudo systemctl enable weather
```
- if the file `/etc/systemd/system/weather.service` changes, run:
```
  sudo systemctl daemon-reload
```
- to start the service:
```
  sudo systemctl start weather
  sudo systemctl status weather
```
