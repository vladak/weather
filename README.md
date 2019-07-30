# weather

Simple weather monitoring Python script

## Install

- needs pip3:
```
    sudo apt-get install -y python3-pip
```
- needs OWFS:
```
sudo apt-get -y install python3-ow
```
- clone the repository to `/srv/`:
```
    git clone https://github.com/vladak/weather.git /srv/weather
```
- copy `weather.service` file to `/etc/systemd/system/PiOLED.service`:
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
