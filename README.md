# SteuerungsZentrale

## Intro ##

Zentrale IOT Steuerung.
Der erste Teil soll die Rollos tagsüber bei Hitze weitgehend schliessen.



## Hardware ##

### Sensor ###
Es Aussensensor gibt die Werte per MQTT an alle interessierten.
Implementierung siehe Projekt Sensor-Basis.

### Aktoren ###
Neu entdeckt: Shelly 2.5. Kleines Gerät, dass sich hinter den Rollo-Schaltern einbauen lässt.
Unterstützt unter anderem MQTT

### Host ###
Das Programm (siehe unten) braucht natürlich einen Host, auf dem es laufen soll.
Plan ist, einen Dockercontainer zu bauen, der dann auf meinem NAS laeuft

## Software ##

Die Steuerung soll in Python implementiert werden.
Hauptgrund ist die einfache Anbindung an MQTT und die Möglichkeit, das in einem Dockercontainer auf das NAS zu bringen.
Da der MQTT-Broker (Mosquitto) ebenfalls auf dem NAS läuft, sollte das eigentlich klappen

### Libraries ###

zum Betrieb sind einige Bibliotheken nötig:

#### paho-mqtt ####
*import paho.mqtt.client as mqtt*
MQTT Nachrichten senden und empfangen.

#### time ####
*import time*
Um die Aktualität von MQTT Nachrichten zu speichern

#### logging ####
*import logging*
*import optparse*
Zur Unterstützung beim entwickeln

### Klassen ###

#### MqttNachricht ####
Die generelle Klasse fuer Mqtt-Nachrichten.
Im Konstrutkor bekommt sie einen Namen und ein Pattern
Sie merkt sich den Wert und wann der Wert gekommen ist, damit kann sie pruefen, ob der Wert ueberhaupt aktuell ist.

Internes Format: Nachricht=["zeit","wert","pattern","name", "timeout"]

Es kann auch ein individueller Timeout gesetzt werden (Sensor schickt nur alle 5 Minuten oder ggfs sogar nur alle Stunde, Shelly alle 30s)

#### MqttTemperatur ####
Ist eine Spezialisierung von MqttNachricht.
In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist

#### Shelly ####
Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln



