# SteuerungsZentrale

## Intro ##

Zentrale IOT Steuerung.
Der erste Teil soll die Rollos tagsüber bei Hitze weitgehend schliessen.
Oder nachts(morgens), wenn es dämmert, den Rollo schliessen.


## Hardware ##

### Sensor ###
Ein Aussensensor schickt die Werte per MQTT an alle interessierten.
Implementierung siehe Projekt Sensor-Basis.

### Aktoren ###
Neu entdeckt: Shelly 2.5. Kleines Gerät, dass sich hinter den Rollo-Schaltern einbauen lässt.
Unterstützt unter anderem MQTT

### Host ###
Das Programm (siehe unten) braucht natürlich einen Host, auf dem es laufen soll.
Plan ist, einen Dockercontainer zu bauen, der dann auf meinem NAS läuft

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

#### threading ####
*import threading*
Bei MQTT-Nachrichten ist die Reihenfolge der Nachrichten nicht garantiert.
Aussder soll der Shelly nicht permanent seinen Status schicken, sondern nur auf Bedarf (auf besonderen Wunsch meiner Besten Hälfte).
Das lässt sich am Besten mit Threads realisieren (wenn noch kein aktueller Wert da ist, einen besorgen. Um beim Warten nicht zu blockieren gibt es 
je Aktivität einen eigenen Thread.

### Klassen ###

#### MqttNachricht ####
Die generelle Klasse fuer Mqtt-Nachrichten.
Im Konstrutkor bekommt sie einen Namen und ein Pattern
Sie merkt sich den Wert und wann der Wert gekommen ist, damit kann sie pruefen, ob der Wert ueberhaupt aktuell ist.

Es kann auch ein individueller Timeout gesetzt werden (Sensor schickt nur alle 5 Minuten oder ggfs sogar nur alle Stunde, Shelly alle 30s)

##### MqttSensor #####
##### MqttShelly #####
Der Sensor und die Shellys haben unterschiedliche Verhalten bzgl. der MQTT-Nachrichten.
Beim Sensor kommen die Werte quasi zeitgleich, aber die Reihenfolge ist nicht klar.
Da reicht es kurz zu warten, bis alle Werte da sind.
Beim Shelly muss ein MQTT-Timeout gesetzt werden (und rückgesetzt) und dann kommen die Werte erst.
Dafür haben die beiden Klassen jeweils eine eigene Implementierung von *Gueltig*

#### MqttTemperatur ####
Ist eine Spezialisierung von MqttNachricht.
In der Update-Methode wird geprüft, ob der Rollo zu bewegen ist

#### MqttHelligkeit ####
Ist eine Spezialisierung von MqttNachricht.
In der Update-Methode wird geprüft, ob der Rollo zu bewegen ist

#### Shelly ####
Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle bündeln


### Multi-Threading ###
Problem: wenn eine mqtt-Nachricht kommt, ist nicht klar, ob die anderen Meldungen vorher gekommen sind oder erst danach kommen.
Ausserdem soll der Shelly nicht regelmäßig senden, sondern nur auf Anforderung.
Das erfordert eine Synchronisierung.
Der neue Ansatz: Im Main-Thread läuft die mqtt-Schleife. Wenn eine "Aktionable" Nachricht kommt (z.B. ein Temperaturwert > Schwelle, zur richtigen Zeit, bei genug Helligkeit),
wird ein Thread gestartet, in dem die anderen Werte geprüft werden. Sollten die Werte da noch nicht vorliegen, kann der Thread auf sie warten, ohne zu blockieren
(insbesondere den weiteren Empfang von mqtt-Nachrichten). Gerade für den Shelly kann das Verschicken der mqtts getriggert werden  (Start + Stop).
Sollten die Werte nicht nach einer festen Zeit vorliegen, wird ein Fehler protokolliert.

Dazu muss in "Gueltig" ein gültiger Wert kommen. Die Implementierung ist abhängig von der Quelle, Sensor-Werte müssen (kurz) warten,
Shelly-Werte müssen - falls nicht vor kurzem ein Trigger gesetzt wurde - einen Trigger setzen, und dann auf die Nachricht warten.

### Internas ###
#### Format der Log-Messages: ####
csv (Komma Separierte Werte)
Spalte | Inhalt | nur im Docker-Log
0 | Zeit | x
1 | Output-Kanal | x
3 | Log-Level | 
4 | Klasse | 
5 | Objekt | 
6 | Zeit/Wert/Nachricht |


