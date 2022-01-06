# weather

Simple weather monitoring Python script. Collects the temperature data using
OWFS and barometric pressure data using BME280 sensor connected via I2C,
uses Prometheus web server to export the data.

## Install

```
- needs OWFS system package:
```
  sudo apt-get -y install owfs
```
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
  cp weather.service /etc/systemd/system/weather.service
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
