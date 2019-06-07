#!/usr/bin/env python
# -*- coding: cp1252 -*-
from __future__ import print_function

import os
import logging
import optparse
import time
import urllib2
import threading

import paho.mqtt.client as mqtt

# Debug Level: Liste mit Klassennamen, in der Print-Methode wird geprueft, ob der Name in der Liste ist
# Falls ja: print, falls nein: nix
Debug_Klasse = [ "MqttNachricht", "MqttTemperatur", "MqttHelligkeit", "Shelly" ]
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

    def __init__(self, pattern, name, timeout):
        self._Zeitpunkt = 0     # Variabel, Zugriff muss Threadsafe sein
        self._Wert = ""         # Variabel, Zugriff muss Threadsafe sein
        self._Pattern = pattern # Konstant
        self.Name = name       # Konstant
        self.Timeout = timeout # Konstant
        self.MeinLock = threading.RLock()
        global _Mqtt_Topic_Liste
        _Mqtt_Topic_Liste.append(self)
        for i in Debug_Klasse:
            if i == self.__class__.__name__:
                logger.debug("MqttNachricht,  {name:16s}, neues Pattern {pattern:s}".format(
                      name=self.Name,
                      pattern=self._Pattern))

    def Gueltig(self):
        self.MeinLock.acquire()
        Z = self._Zeitpunkt
        self.MeinLock.release()
        if time.time() - Z < self.Timeout:
            return True
        else:
            return False

    def Update(self, p, n):
        if self._Pattern == p:
            self.MeinLock.acquire()
            self._Zeitpunkt = time.time()
            self._Wert = n
            self.MeinLock.release()
            self.Print()
            return True
        else:
            return False

    def Match(self, p):
        return self._Pattern == p

    def Wert(self):
        self.MeinLock.acquire()
        W = self._Wert
        self.MeinLock.release()
        return W

    def Zeit(self):
        self.MeinLock.acquire()
        T = self._Zeitpunkt
        self.MeinLock.release()
        return T

    def Print(self):
        for i in Debug_Klasse:
            if i == self.__class__.__name__:
                self.MeinLock.acquire()
                logger.debug("MqttNachricht,  {name:16s}, t: {zeit:8s}0, v: {wert:8s}, {gueltig:3s}".format(
                      name=self.Name,
                      zeit=time.strftime("%X",time.localtime(self._Zeitpunkt)),
                      wert=self._Wert,
                      gueltig=" ok" if self.Gueltig() else "alt"))
                self.MeinLock.release()
                return

    def Subscribe(self):
        if not client.subscribe(self._Pattern):
            logger.error("MqttNachricht,  {name:16s}, Fehler bei subscribe to {s:s}".format(
                name=self.Name,
                s=self._Pattern))
        else:
            logger.debug("MqttNachricht,  {name:16s}, subscribed to {s:s}".format(
                name=self.Name,
                s=self._Pattern))

class MqttTemperatur(MqttNachricht):
    """MqttTemperatur
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist.
    """

    def __init__(self,pattern,name):
        self.LetzteAktivitaet = 0 # Variabel, Zugriff muss Threadsafe sein
        MqttNachricht.__init__(self, pattern, name, 100) # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz

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
            self.MeinLock.acquire()
            T = float(self._Wert)
            Aktivitaetstimeout = time.time() - self.LetzteAktivitaet
            self.MeinLock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            # Ist es heiss genug, passt die Zeit und wurde der Shelly in der letzten Stunde nicht verfahren
            if (T > 27.0) and (stunde > 10) and (stunde < 20) and (Aktivitaetstimeout > 3600):
                logger.debug("MqttTemperatur, {name:16s}, Temperatur und Zeit passt".format(
                    name=self.Name))
                ###### HIER MUSS DER WECHSEL IN EINEN NEUEN THREAD ERFOLGEN - BEVOR AUF EINEN ANDEREN MQTT-WERT ZUGEGRIFFEN WIRD
                if not _Status_Helligkeit.Gueltig():
                    logger.warning("MqttTemperatur, {name:16s}, Kein Helligkeitswert".format(
                        name=self.Name))
                    return
                H = int(_Status_Helligkeit.Wert())
                if (H < 3000):
                    logger.debug("MqttTemperatur, {name:16s}, zu dunkel h: {hell:d}".format(
                        name=self.Name,
                        hell=H))
                    return
                if _Shelly_SZ.Bereit_Oben():
                    logger.debug("MqttTemperatur, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                        name=self.Name))
                    for i in _Mqtt_Topic_Liste:
                        i.Print()
                    _Shelly_SZ.Fahre_Weitgehend_Zu()
                    self.MeinLock.acquire()
                    self.LetzteAktivitaet = time.time()
                    self.MeinLock.release()
                else:
                    logger.debug("MqttTemperatur, {name:16s}, Shelly nicht bereit".format(
                        name=self.Name))

class MqttHelligkeit(MqttNachricht):
    """MqttHelligkeit
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist
    """

    def __init__(self,pattern,name):
        self.LetzteAktivitaet = 0
        MqttNachricht.__init__(self, pattern, name, 100) # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz

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
            self.MeinLock.acquire()
            H = int(self._Wert)
            Aktivitaetstimeout = time.time() - self.LetzteAktivitaet
            self.MeinLock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            # Ist es hell genug, passt die Zeit und wurde der Shelly in der letzten Stunde nicht verfahren
            if (H > 50) and (H < 500) and (stunde > 3) and (stunde < 6) and (Aktivitaetstimeout > 3600):
                logger.debug("MqttHelligkeit, {name:16s}, Helligkeitsbereich und Zeit passt".format(
                    name=self.Name))
                ###### HIER MUSS DER WECHSEL IN EINEN NEUEN THREAD ERFOLGEN - BEVOR AUF EINEN ANDEREN MQTT-WERT ZUGEGRIFFEN WIRD
                if _Shelly_SZ.Bereit_Oben():
                    logger.debug("MqttHelligkeit, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                        name=self.Name))
                    for i in _Mqtt_Topic_Liste:
                        i.Print()
                    _Shelly_SZ.Fahre_Zu()
                    self.MeinLock.acquire()
                    self.LetzteAktivitaet = time.time()
                    self.MeinLock.release()
                else:
                    logger.debug("MqttHelligkeit, {name:16s}, Shelly nicht bereit".format(
                        name=self.Name))

class Shelly:
    """Shelly
    Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln
    """
    def __init__(self,pattern,name):
        self.Name = name
        self._Pattern = pattern
        self.Schalter_0 = MqttNachricht(pattern + "/input/0",      name + "-Schalter 0", 300) # Werte bleiben 5 Minuten gueltig
        self.Schalter_1 = MqttNachricht(pattern + "/input/1",      name + "-Schalter 1", 300)
        self.Rollo      = MqttNachricht(pattern + "/roller/0",     name + "-Rollo Stat", 300)
        self.Rollo_Pos  = MqttNachricht(pattern + "/roller/0/pos", name + "-Rollo Pos ", 300)
        self.Print()

    def Print(self):
        for i in Debug_Klasse:
            if i == self.__class__.__name__:
                logger.debug("Shelly,         {name:16s}, Basis-Pattern {wert:s}".format(
                      name=self.Name,
                      wert=self._Pattern))
                return

    def RequestMqttMessage(self):
        """Shelly:RequestMqttMessage
        Falls kein gueltiger Mqtt-Wert da ist, kann man einen Trigger setzen, um einen neuen Wert zu bekommen.
        Das wird per http-Request gemacht: mqtt-Timeout auf 1 (s) setzen, dann wieder auf 0 (d.h. nie)
        In der nächsten Schleife sollten dann gueltige Werte da sein.
        """
        urllib2.urlopen("http://192.168.2.49/settings?mqtt_update_period=1")
        time.sleep(5)
        urllib2.urlopen("http://192.168.2.49/settings?mqtt_update_period=0")

    def Bereit_Oben(self):
        """Shelly:Bereit
        Hier wird geprueft, ob der Shelly aktuelle Werte hat, die Schalter auf 0 stehen und kein Motor laeuft.
        In dem Fall ist 'Automatik-Mode' an.
        """
        if not self.Schalter_0.Gueltig():
            logger.info("Shelly,         {name:16s}, Schalter(0) kein aktueller Wert".format(name=self.Name))
            self.RequestMqttMessage()
            return False
        if self.Schalter_0.Wert() != "0":
            logger.debug("Shelly,         {name:16s}, Schalter(0) nicht aus, Wert: {wert:s}".format(name=self.Name,wert=self.Schalter_0.Wert()))
            return False
        if not self.Schalter_1.Gueltig():
            logger.warning("Shelly,         {name:16s}, Schalter(1) kein aktueller Wert".format(name=self.Name))
            self.RequestMqttMessage()
            return False
        if self.Schalter_1.Wert() != "0":
            logger.debug("Shelly,         {name:16s}, Schalter(1) nicht aus, Wert: {wert:s}".format(name=self.Name,wert=self.Schalter_1.Wert()))
            return False
        if not self.Rollo.Gueltig():
            logger.warning("Shelly,         {name:16s}, Rollo kein aktueller Wert".format(name=self.Name))
            self.RequestMqttMessage()
            return False
        if self.Rollo.Wert() != "stop":
            logger.debug("Shelly,         {name:16s}, Rollo laeuft, Wert: {wert:s}".format(name=self.Name,wert=self.Rollo.Wert()))
            self.RequestMqttMessage()
            return False
        if not self.Rollo_Pos.Gueltig():
            logger.warning("Shelly,         {name:16s}, Rollo_Pos kein aktueller Wert".format(name=self.Name))
            self.RequestMqttMessage()
            return False
        if self.Rollo_Pos.Wert() != "100":
            logger.debug("Shelly,         {name:16s}, Rollo Position nicht oben, Wert: {wert:s}".format(name=self.Name,wert=self.Rollo_Pos.Wert()))
            return False
        # final, alle Tests bestanden, return true :-)
        logger.debug("Shelly,         {name:16s}, Bereit_Oben erfolgreich".format(name=self.Name))
        return True

    def Fahre_Weitgehend_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo weitgehend (20% Rest) zu zu fahren
        """
        logger.debug("In Fahre_Weitgehend_Zu : tue nix")
        return
        if client.publish(self._Pattern + "/roller/0/command/pos", "40"):
            logger.info(" Shelly,         {name:16s},  Rollo wird auf 40% gefahren".format(name=self.Name))
        else:
            logger.error("Shelly,         {name:16s},  mqtt message failed".format(name=self.Name))

    def Fahre_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo zu schliessen
        """
        logger.debug("In Fahre_Zu : tue nix")
        return
        if client.publish(self._Pattern + "/roller/0/command", "close"):
            logger.info(" Shelly,         {name:16s},   Rollo wird geschlossen".format(name=self.Name))
        else:
            logger.error("Shelly,         {name:16s},   mqtt message failed".format(name=self.Name))

def on_connect(client, userdata, flags, rc):
    logger.info(" on_connect,                     , Connected with result code " + str(rc))
    for i in _Mqtt_Topic_Liste:
        i.Subscribe()

def on_message(client, userdata, msg):
    for i in _Mqtt_Topic_Liste:
        if i.Update(msg.topic, msg.payload):
            break

def main():
    # missing timezone info when running in docker
    os.environ['TZ'] = 'Europe/Berlin'

    parser = optparse.OptionParser()
    parser.add_option('-l', '--logging-level', help='Logging level')
    parser.add_option('-f', '--logging-file', help='Logging file name')
    (options, args) = parser.parse_args()
    logging_level = LOGGING_LEVELS.get(options.logging_level, logging.NOTSET)
    logging.basicConfig(level=logging_level, filename=options.logging_file,
                      format='%(levelname)s, %(message)s')

    global logger
    logger = logging.getLogger()

    global _Status_Helligkeit
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//H">34.1</MqttMessage>
    _Status_Helligkeit = MqttHelligkeit("Sensor/WZTuF/EG/WZ//H", "Aussen-Hellig")
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//T">34.1</MqttMessage>
    _Status_Temperatur = MqttTemperatur("Sensor/WZTuF/EG/WZ//T", "Aussen-Temp   ")

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
