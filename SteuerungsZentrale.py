#!/usr/bin/env python
# -*- coding: cp1252 -*-
from __future__ import print_function

import time
import paho.mqtt.client as mqtt

class MqttNachricht:
    """MqttNachricht
    Die generelle Klasse fuer Mqtt-Nachrichten.
    Im Konstrutkor bekommt sie einen Namen und ein Pattern
    Sie merkt sich den Wert und wann der Wert gekommen ist, damit kann sie pruefen, ob der Wert ueberhaupt aktuell ist.

    Internes Format: Nachricht=["zeit","wert","pattern","name", "timeout"]

    Es kann auch ein individueller Timeout gesetzt werden (Sensor schickt nur alle 5 Minuten oder ggfs sogar nur alle Stunde, Shelly alle 30s)
    """

    def __init__(self,pattern,name):
        self.Nachricht = [0,"",pattern,name,60]
        self.Print()

    def Gueltig(self):
        if time.time() - self.Nachricht[0] < self.Nachricht[4]:
            return True
        else:
            return False

    def Update(self,n):
        self.Nachricht[0] = time.time()
        self.Nachricht[1] = n

    def Update(self, p, n):
        if self.Nachricht[2] == p:
            self.Nachricht[0] = time.time()
            self.Nachricht[1] = n
            self.Print()
            return True
        else:
            return False

    def Match(self, p):
        return self.Nachricht[2] == p

    def Wert(self):
        return self.Nachricht[1]

    def Zeit(self):
        return self.Nachricht[0]

    def SetzeTimeout(self, t):
        self.Nachricht[4] = t

    def Print(self):
        print("Mqtt: {name:16s} t: {zeit:8s} v: {wert:8}".format(
              name=self.Nachricht[3],
              zeit=time.strftime("%X",time.localtime(self.Nachricht[0])),
              wert=self.Nachricht[1]), end=" ")
        if self.Gueltig():
            print("  gueltig", end=" ")
        else:
            print("ungueltig", end=" ")
        print("Pattern: ", self.Nachricht[2])

    def Subscribe(self):
        client.subscribe(self.Nachricht[2])
        print("Subscribed to", self.Nachricht[2])

class MqttTemperatur(MqttNachricht):
    """MqttTemperatur
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist
    """

    def Update(self, p, n):
        """MqttTemperatur.Update
        Hier wird geprueft, ob die Bedingungen erfuellt sind, den Rollo runterzufahren.
        - Sind alle anderen benoetigten Werte da und aktuell?
        - Ist die Temperatur ueber der Hitze-Schwelle
        - Ist es ueberhaupt Tag (runterfahren auf der Westseite erst ab 11:00 Uhr bis max 21:00 Uhr)
        - Ist es hell (scheint die Sonne?)
        - Sind die Rollo-Schalter auf "aus" (sonst keine Automatik)
        - Der Rollo laeuft nicht
        - Der Rollo ist oben (Position 0)        
        """
        # Erste Schritt: Basis-Klasse aufrufen
        if MqttNachricht.Update(self,p,n):
            print("Temperatur: Checke und tue...")
            global _Status_Liste
            for i in _Status_Liste:
                if not i.Gueltig():
                    print("Keine gueltigen Werte, mache nix")
                    return
            if self.Wert() > 25:
                print("Warm")
                zeit = time.localtime()
                stunde = zeit.tm_hour
                if (stunde < 11) or (stunde > 21):
                    print("Ausserhalb der Uhrzeiten")
                    return
                global _Status_Helligkeit
                if (int(_Status_Helligkeit.Wert()) < 3000):
                    print("Draussen ist dunkel (oder zumindest keine Sonne)")
                    return
                global _Status_Schalter_0, _Status_Schalter_1, _Status_Rollo, _Status_Rollo_Pos
                if _Status_Schalter_0.Wert() != "0":
                    print("Schalter(0) nicht aus")
                    print("Wert:",_Status_Schalter_0.Wert())
                    return
                if _Status_Schalter_1.Wert() != "0":
                    print("Schalter(1) nicht aus")
                    print("Wert:",_Status_Schalter_1.Wert())
                    return
                if _Status_Rollo.Wert() != "stop":
                    print("Rollo laeuft")
                    print("Wert:",_Status_Rollo.Wert())
                    return
#                if _Status_Rollo_Pos.Wert() != "0":
                if _Status_Rollo_Pos.Wert() != "-1":
                    print("Rollo Position nicht oben")
                    print("Wert:",_Status_Rollo_Pos.Wert())
                    return
                print("Alle Bedingungen passen, fahre Rollo runter")
                global client
                client.publish("shellies/shellyswitch25-745815/roller/0/command", "close")
#                client.publish("shellies/shellyswitch25-745815/roller/0/command/pos", "80")


def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))
    global _Status_Liste
    for i in _Status_Liste:
        i.Subscribe()

def on_message(client, userdata, msg):
    global _Status_Liste
    global _Status_Temperatur
    for i in _Status_Liste:
        if i.Update(msg.topic, msg.payload):
            break
    
    if _Status_Temperatur.Match(msg.topic):
        print("T=", end="")

# MQTT Daten
#<MqttMessage topic="shellies/shellyswitch25-745815/input/0">0</MqttMessage>
_Status_Schalter_0 = MqttNachricht("shellies/shellyswitch25-745815/input/0", "Schalter 0")
#<MqttMessage topic="shellies/shellyswitch25-745815/input/1">0</MqttMessage>
_Status_Schalter_1 = MqttNachricht("shellies/shellyswitch25-745815/input/1", "Schalter 1")
#<MqttMessage topic="shellies/shellyswitch25-745815/roller/0">stop</MqttMessage>
_Status_Rollo = MqttNachricht("shellies/shellyswitch25-745815/roller/0", "Rollo Status")
#<MqttMessage topic="shellies/shellyswitch25-745815/roller/0/pos">-1</MqttMessage>
_Status_Rollo_Pos = MqttNachricht("shellies/shellyswitch25-745815/roller/0/pos", "Rollo Position")
#<MqttMessage topic="Sensor/WZTuF/EG/WZ//H">34.1</MqttMessage>
_Status_Helligkeit = MqttNachricht("Sensor/WZTuF/EG/WZ//H", "Helligkeit")
#<MqttMessage topic="Sensor/WZTuF/EG/WZ//T">34.1</MqttMessage>
_Status_Temperatur = MqttTemperatur("Sensor/WZTuF/EG/WZ//T", "Temp Aussen")

_Status_Liste = [ _Status_Schalter_0, _Status_Schalter_1, _Status_Rollo, _Status_Rollo_Pos, _Status_Helligkeit, _Status_Temperatur ]

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect("diskstation.fritz.box", 1883, 60)

client.loop_forever()
