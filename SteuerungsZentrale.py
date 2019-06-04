#!/usr/bin/env python
# -*- coding: cp1252 -*-
from __future__ import print_function

import logging
import optparse

import time
import paho.mqtt.client as mqtt

# Debug Level: Liste mit Klassennamen, in der Print-Methode wird geprueft, ob der Name in der Liste ist
# Falls ja: print, falls nein: nix
Debug_Klasse = [ "MqttNachricht", "MqttTemperatur", "Shelly" ]
LOGGING_LEVELS = {'critical': logging.CRITICAL,
                  'error': logging.ERROR,
                  'warning': logging.WARNING,
                  'info': logging.INFO,
                  'debug': logging.DEBUG}


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
        global _Mqtt_Topic_Liste
        _Mqtt_Topic_Liste.append(self)
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
        for i in Debug_Klasse:
            if i == self.__class__.__name__:
                logger.info("Mqtt: {name:16s} t: {zeit:8s} v: {wert:8s} {gueltig:9s} Pattern {pattern:s}".format(
                      name=self.Nachricht[3],
                      zeit=time.strftime("%X",time.localtime(self.Nachricht[0])),
                      wert=self.Nachricht[1],
                      gueltig="  gueltig" if self.Gueltig() else "ungueltig",
                      pattern=self.Nachricht[2]))
                return

    def Subscribe(self):
        if not client.subscribe(self.Nachricht[2]):
            logger.error("Fehler bei subscribe to {s:s}".format(s=self.Nachricht[2]))
        else:
            logger.info("subscribed to {s:s}".format(s=self.Nachricht[2]))

class MqttTemperatur(MqttNachricht):
    """MqttTemperatur
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist.
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
        # Erster Schritt: Basis-Klasse aufrufen
        if MqttNachricht.Update(self,p,n):
            if self.Wert() > 25:
                logger.info("Temperaturschwelle erreicht")
                zeit = time.localtime()
                stunde = zeit.tm_hour
                if (stunde < 11) or (stunde > 20):
                    logger.info("Ausserhalb der Uhrzeiten")
                    return
                if not _Status_Helligkeit.Gueltig():
                    logger.warning("Kein Helligkeitswert")
                    return
                if (int(_Status_Helligkeit.Wert()) < 3000):
                    logger.info("Draussen ist dunkel (oder zumindest keine Sonne)")
                    return
                if _Shelly_SZ.Bereit_Oben():
                    logger.info("Alle Bedingungen passen, fahre Rollo runter")
                    _Shelly_SZ.Fahre_Weitgehend_Zu()
                else:
                    logger.info("Shelly nicht bereit")

class MqttHelligkeit(MqttNachricht):
    """MqttHelligkeit
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist
    """

    def Update(self, p, n):
        """MqttHelligkeit.Update
        Hier wird geprueft, ob die Bedingungen erfuellt sind, den Rollo runterzufahren.
        - Sind alle anderen benoetigten Werte da und aktuell?
        - Daemmert es, d.h. die Helligkeit ist ueber einer Schwelle und unter einer anderen
        - Ist es frueh am Tag (runterfahren, wenn das Fenster nachts auf war)
        - Sind die Rollo-Schalter auf "aus" (sonst keine Automatik)
        - Der Rollo laeuft nicht
        - Der Rollo ist oben (Position 0)
        """
        # Erster Schritt: Basis-Klasse aufrufen
        if MqttNachricht.Update(self,p,n):
            if (int(self.Wert()) > 50) and (int(self.Wert() < 500)):
                logger.info("Helligkeitsbereich erreicht")
                zeit = time.localtime()
                stunde = zeit.tm_hour
                if (stunde < 3) or (stunde > 6):
                    logger.info("Ausserhalb der Uhrzeiten")
                    return
                if _Shelly_SZ.Bereit_Oben():
                    logger.info("Alle Bedingungen passen, fahre Rollo runter")
                    _Shelly_SZ.Fahre_Zu()
                else:
                    logger.info("Shelly nicht bereit")

class Shelly:
    """Shelly
    Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln
    """
    def __init__(self,pattern,name):
        self.Name = name
        self.Pattern = pattern
        self.Schalter_0 = MqttNachricht(pattern + "/input/0", name + "Schalter 0")
        self.Schalter_1 = MqttNachricht(pattern + "/input/1", name + "Schalter 1")
        self.Rollo = MqttNachricht(pattern + "/roller/0", name + "Rollo Status")
        self.Rollo_Pos = MqttNachricht(pattern + "/roller/0/pos", name + "Rollo Position")
        self.Print()

    def Print(self):
        for i in Debug_Klasse:
            if i == self.__class__.__name__:
                logger.info("Shelly: {name:16s} Pattern: {wert:8}".format(
                      name=self.Name,
                      wert=self.Pattern))
                self.Schalter_0.Print()
                self.Schalter_1.Print()
                self.Rollo.Print()
                self.Rollo_Pos.Print()
                return

    def Bereit_Oben(self):
        """Shelly:Bereit
        Hier wird geprueft, ob der Shelly aktuelle Werte hat, die Schalter auf 0 stehen und kein Motor laeuft.
        In dem Fall ist 'Automatik-Mode' an.
        """
        print("Bereit_Oben")
        if not self.Schalter_0.Gueltig():
            logger.warning("{name:s} Schalter(0) kein aktueller Wert".format(name=self.Name))
            return False
        if self.Schalter_0.Wert() != "0":
            logger.info("{name:s} Schalter(0) nicht aus, Wert: {wert:s}".format(name=self.Name,wert=self.Schalter_0.Wert()))
            return False
        if not self.Schalter_1.Gueltig():
            logger.warning("{name:s} Schalter(1) kein aktueller Wert".format(name=self.Name))
            return False
        if self.Schalter_1.Wert() != "0":
            logger.info("{name:s} Schalter(1) nicht aus, Wert: {wert:s}".format(name=self.Name,wert=self.Schalter_1.Wert()))
            return False
        if not self.Rollo.Gueltig():
            logger.warning("{name:s} Rollo kein aktueller Wert".format(name=self.Name))
            return False
        if self.Rollo.Wert() != "stop":
            logger.info("{name:s} Rollo laeuft, Wert: {wert:s}".format(name=self.Name,wert=self.Rollo.Wert()))
            return False
        if not self.Rollo_Pos.Gueltig():
            logger.warning("{name:s} Rollo_Pos kein aktueller Wert".format(name=self.Name))
            return False
        if self.Rollo_Pos.Wert() != "100":
            logger.info("{name:s} Rollo Position nicht oben, Wert: {wert:s}".format(name=self.Name,wert=self.Rollo_Pos.Wert()))
            return False
        # final, alle Tests bestanden, return true :-)
        logger.info("{name:s} Bereit_Oben: True".format(name=self.Name))
        return True

    def Fahre_Weitgehend_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo weitgehend (20% Rest) zu zu fahren
        """
        if client.publish(self.Pattern + "/roller/0/command/pos", "40"):
            logger.info("{name:s}  Rollo wird auf 40% gefahren".format(name=self.Name))
        else:
            logger.error("{name:s}  mqtt message failed".format(name=self.Name))

    def Fahre_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo zu schliessen
        """
        if client.publish(self.Pattern + "/roller/0/command", "close"):
            logger.info("{name:s}   Rollo wird geschlossen".format(name=self.Name))
        else:
            logger.error("{name:s}   mqtt message failed".format(name=self.Name))

def on_connect(client, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))
    for i in _Mqtt_Topic_Liste:
        i.Subscribe()

def on_message(client, userdata, msg):
    for i in _Mqtt_Topic_Liste:
        if i.Update(msg.topic, msg.payload):
            break

def main():
    parser = optparse.OptionParser()
    parser.add_option('-l', '--logging-level', help='Logging level')
    parser.add_option('-f', '--logging-file', help='Logging file name')
    (options, args) = parser.parse_args()
    logging_level = LOGGING_LEVELS.get(options.logging_level, logging.NOTSET)
    logging.basicConfig(level=logging_level, filename=options.logging_file,
                      format='%(asctime)s %(levelname)s: %(message)s',
                      datefmt='%Y-%m-%d %H:%M:%S')
    global logger
    logger = logging.getLogger()

#    global _Mqtt_Topic_Liste
    global _Status_Helligkeit
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//H">34.1</MqttMessage>
    _Status_Helligkeit = MqttHelligkeit("Sensor/WZTuF/EG/WZ//H", "Helligkeit")
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//T">34.1</MqttMessage>
    _Status_Temperatur = MqttTemperatur("Sensor/WZTuF/EG/WZ//T", "Temp Aussen")

    global _Shelly_SZ
    _Shelly_SZ = Shelly("shellies/shellyswitch25-745815", "SZ")

    global client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect("diskstation.fritz.box", 1883, 60)

    client.loop_forever()

# das sind die globalen Variablen..
# Für die Callbacks von mqtt und MqttNachricht zum append..
_Mqtt_Topic_Liste = []
# wird von MqttTemperatur genutzt:
_Status_Helligkeit = []
_Shelly_SZ = []
# globales logging
logger = []
# mqtt client (MqttNachricht: zum subscribe, Shelly: zum publish)
client = []

if __name__ == '__main__':
    main()
