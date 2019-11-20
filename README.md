# weather

Simple weather monitoring Python script. Collects the temperature data using
OWFS, uses Prometheus to export the data.

## Install

- needs OWFS:
```
    sudo apt-get -y install python3-ow
```
- This needs Prometheus Python client API library:
```
sudo apt-get install python3-prometheus-client
```
- clone the repository to `/srv/`:
```
    git clone https://github.com/vladak/weather.git /srv/weather
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
