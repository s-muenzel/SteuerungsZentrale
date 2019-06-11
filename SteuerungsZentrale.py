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
__Debug_Klasse = [ "MqttNachricht", "MqttTemperatur", "MqttHelligkeit", "MqttShelly", "Shelly" ]
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
        self._Name = name       # Konstant
        self._Timeout = timeout # Konstant
        self._GueltigEvent = threading.Event()
        self._Lock = threading.RLock()
        global __Mqtt_Topic_Liste
        __Mqtt_Topic_Liste.append(self)
        for i in __Debug_Klasse:
            if i == self.__class__.__name__:
                __logger.debug("MqttNachricht,  {name:16s}, neues Pattern {pattern:s}".format(
                      name=self._Name,
                      pattern=self._Pattern))

    def Gueltig(self):
        self._Lock.acquire()
        Z = self._Zeitpunkt
        self._Lock.release()
        if time.time() - Z < self._Timeout:
            return True
        else:
            self._GueltigEvent.clear()
            return False

    def Update(self, p, n):
        if self._Pattern == p:
            self._Lock.acquire()
            self._Zeitpunkt = time.time()
            self._Wert = n
            self._Lock.release()
            self._GueltigEvent.set()
            self.Print()
            return True
        else:
            return False

    def Match(self, p):
        return self._Pattern == p

    def Wert(self):
        self._Lock.acquire()
        W = self._Wert
        self._Lock.release()
        return W

    def Zeit(self):
        self._Lock.acquire()
        T = self._Zeitpunkt
        self._Lock.release()
        return T

    def Print(self):
        for i in __Debug_Klasse:
            if i == self.__class__.__name__:
                self._Lock.acquire()
                __logger.debug("MqttNachricht,  {name:16s}, t: {zeit:8s}0, v: {wert:8s}, {gueltig:3s}".format(
                      name=self._Name,
                      zeit=time.strftime("%X",time.localtime(self._Zeitpunkt)),
                      wert=self._Wert,
                      gueltig=" ok" if self.Gueltig() else "alt"))
                self._Lock.release()
                return

    def Subscribe(self):
        if not __mqttclient.subscribe(self._Pattern):
            __logger.error("MqttNachricht,  {name:16s}, Fehler bei subscribe to {s:s}".format(
                name=self._Name,
                s=self._Pattern))
        else:
            __logger.debug("MqttNachricht,  {name:16s}, subscribed to {s:s}".format(
                name=self._Name,
                s=self._Pattern))

class MqttSensor(MqttNachricht):
    """Zwischenklasse: fuer Werte, die von dem Sensor kommen, gibt es eine Default-Gueltig-Implementierung"""
    def Gueltig(self):
        if not MqttNachricht.Gueltig(self):
            __logger.debug("MqttSensor, {name:16s}, Noch kein gueltiger Wert, warte auf einen".format(
                    name=self._Name))
            self._GueltigEvent.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
            if MqttNachricht.Gueltig(self):
                __logger.debug("MqttSensor, {name:16s}, Jetzt gueltiger Wert".format(
                        name=self._Name))
                return True
            else:
                __logger.warning("MqttSensor, {name:16s}, Immer noch kein gueltiger Wert".format(
                    name=self._Name))
                return False
        else:
            return True

class MqttTemperatur(MqttSensor):
    """MqttTemperatur
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist.
    """

    def __init__(self,pattern,name):
        self._AktivitaetsTimer = 0 # Variabel, Zugriff muss Threadsafe sein
        MqttNachricht.__init__(self, pattern, name, 100) # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz

    def Temp_Check_T(self):
        """erster Schritt: die Funktion wird in einem eigenen Thread gestartet"""
        __logger.debug("In neuem Thread")
        if not __Status_Helligkeit.Gueltig():
            __logger.warning("MqttTemperatur, {name:16s}, Kein Helligkeitswert".format(
                name=self._Name))
            return
        H = int(__Status_Helligkeit.Wert())
        if (H < 3000):
            __logger.debug("MqttTemperatur, {name:16s}, zu dunkel h: {hell:d}".format(
                name=self._Name,
                hell=H))
            return
        if __Shelly_SZ.Bereit_Oben():
            __logger.debug("MqttTemperatur, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                name=self._Name))
            for i in __Mqtt_Topic_Liste:
                i.Print()
            __Shelly_SZ.Fahre_Weitgehend_Zu()
            self._Lock.acquire()
            self._AktivitaetsTimer = time.time()
            self._Lock.release()
        else:
            __logger.debug("MqttTemperatur, {name:16s}, Shelly nicht bereit".format(
                name=self._Name))


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
            self._Lock.acquire()
            T = float(self._Wert)
            Aktivitaetstimeout = time.time() - self._AktivitaetsTimer
            self._Lock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            __logger.debug("MqttTemperatur, {name:16s}, T: {t:f} stunde: {s:d} L_A: {a:f}".format(
                    name=self._Name, t=T,s=stunde,a=Aktivitaetstimeout))
            # Ist es heiss genug, passt die Zeit und wurde der Shelly in der letzten Stunde nicht verfahren
            if (T > 27.0) and (stunde > 10) and (stunde < 20) and (Aktivitaetstimeout > 3600):
                __logger.debug("MqttTemperatur, {name:16s}, Temperatur und Zeit passt".format(
                    name=self._Name))
                __logger.debug("Starte neuen Thread")
                threading.Thread(target=self.Temp_Check_T).start()

class MqttHelligkeit(MqttSensor):
    """MqttHelligkeit
    Ist eine Spezialisierung von MqttNachricht.
    In der Update-Methode wird geprueft, ob der Rollo zu bewegen ist
    """

    def __init__(self,pattern,name):
        self._AktivitaetsTimer = 0
        MqttNachricht.__init__(self, pattern, name, 100) # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz
        
    def Hell_Check_T(self):
        """erster Schritt: die Funktion wird in einem eigenen Thread gestartet"""
        __logger.debug("In neuem Thread")
        if __Shelly_SZ.Bereit_Oben():
            __logger.debug("MqttHelligkeit, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                name=self._Name))
            for i in __Mqtt_Topic_Liste:
                i.Print()
            __Shelly_SZ.Fahre_Zu()
            self._Lock.acquire()
            self._AktivitaetsTimer = time.time()
            self._Lock.release()
        else:
            __logger.debug("MqttHelligkeit, {name:16s}, Shelly nicht bereit".format(
                name=self._Name))

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
            self._Lock.acquire()
            H = int(self._Wert)
            Aktivitaetstimeout = time.time() - self._AktivitaetsTimer
            self._Lock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            __logger.debug("MqttHelligkeit, {name:16s}, H: {h:d} stunde: {s:d} L_A: {a:f}".format(
                    name=self._Name, h=H,s=stunde,a=Aktivitaetstimeout))
            # Ist es hell genug, passt die Zeit und wurde der Shelly in der letzten Stunde nicht verfahren
            if (H > 50) and (H < 500) and (stunde > 3) and (stunde < 6) and (Aktivitaetstimeout > 3600):
                __logger.debug("MqttHelligkeit, {name:16s}, Helligkeitsbereich und Zeit passt".format(
                    name=self._Name))
                __logger.debug("Starte neuen Thread")
                threading.Thread(target=self.Hell_Check_T).start()


class MqttShelly(MqttNachricht):
    """Zwischenklasse: fuer Werte, die von einem Shelly kommen, gibt es eine Default-Gueltig-Implementierung"""
    def __init__(self, pattern, name, timeout, shelly):
        self._Shelly = shelly
        MqttNachricht.__init__(self, pattern, name, timeout)


    def Gueltig(self):
        if not MqttNachricht.Gueltig(self):
            __logger.debug("MqttShelly, {name:16s}, Noch kein gueltiger Wert, fordere neuen an".format(
                    name=self._Name))
            self._Shelly.RequestMqttMessage()
            self._GueltigEvent.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
            if MqttNachricht.Gueltig(self):
                __logger.debug("MqttShelly, {name:16s}, Jetzt gueltiger Wert".format(
                        name=self._Name))
                return True
            else:
                __logger.warning("MqttShelly, {name:16s}, Immer noch kein gueltiger Wert".format(
                    name=self._Name))
                return False
        else:
            return True

class Shelly:
    """Shelly
    Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln
    """
    def __init__(self,pattern,name,ip):
        self._Name = name
        self._Pattern = pattern
        self._IP = ip
        self._Schalter_0 = MqttShelly(pattern + "/input/0",      name + "-Schalter 0", 300, self) # Werte bleiben 5 Minuten gueltig
        self._Schalter_1 = MqttShelly(pattern + "/input/1",      name + "-Schalter 1", 300, self)
        self._Rollo      = MqttShelly(pattern + "/roller/0",     name + "-Rollo Stat", 300, self)
        self._Rollo_Pos  = MqttShelly(pattern + "/roller/0/pos", name + "-Rollo Pos ", 300, self)
        self.Print()

    def Print(self):
        for i in __Debug_Klasse:
            if i == self.__class__.__name__:
                __logger.debug("Shelly,         {name:16s}, Basis-Pattern {wert:s}".format(
                      name=self._Name,
                      wert=self._Pattern))
                return

    def RequestMqttMessage(self):
        """Shelly:RequestMqttMessage
        Falls kein gueltiger Mqtt-Wert da ist, kann man einen Trigger setzen, um einen neuen Wert zu bekommen.
        Das wird per http-Request gemacht: mqtt-Timeout auf 1 (s) setzen, dann wieder auf 0 (d.h. nie)
        In der nächsten Schleife sollten dann gueltige Werte da sein.
        """
        __logger.debug("Shelly,         {name:16s}, RequestMqttMessage".format(name=self._Name))
        urllib2.urlopen("http://"+self._IP + "/settings?mqtt_update_period=2")
        time.sleep(2)
        urllib2.urlopen("http://"+self._IP + "/settings?mqtt_update_period=0")

    def Bereit_Oben(self):
        """Shelly:Bereit
        Hier wird geprueft, ob der Shelly aktuelle Werte hat, die Schalter auf 0 stehen und kein Motor laeuft.
        In dem Fall ist 'Automatik-Mode' an.
        """
        if not self._Schalter_0.Gueltig():
            __logger.warning("Shelly,         {name:16s}, Schalter(0) kein aktueller Wert".format(name=self._Name))
            return False
        if self._Schalter_0.Wert() != "0":
            __logger.debug("Shelly,         {name:16s}, Schalter(0) nicht aus, Wert: {wert:s}".format(name=self._Name,wert=self._Schalter_0.Wert()))
            return False
        if not self._Schalter_1.Gueltig():
            __logger.warning("Shelly,         {name:16s}, Schalter(1) kein aktueller Wert".format(name=self._Name))
            return False
        if self._Schalter_1.Wert() != "0":
            __logger.debug("Shelly,         {name:16s}, Schalter(1) nicht aus, Wert: {wert:s}".format(name=self._Name,wert=self._Schalter_1.Wert()))
            return False
        if not self._Rollo.Gueltig():
            __logger.warning("Shelly,         {name:16s}, Rollo kein aktueller Wert".format(name=self._Name))
            return False
        if self._Rollo.Wert() != "stop":
            __logger.debug("Shelly,         {name:16s}, Rollo laeuft, Wert: {wert:s}".format(name=self._Name,wert=self._Rollo.Wert()))
            self.RequestMqttMessage()
            return False
        if not self._Rollo_Pos.Gueltig():
            __logger.warning("Shelly,         {name:16s}, Rollo_Pos kein aktueller Wert".format(name=self._Name))
            return False
        if self._Rollo_Pos.Wert() != "100":
            __logger.debug("Shelly,         {name:16s}, Rollo Position nicht oben, Wert: {wert:s}".format(name=self._Name,wert=self._Rollo_Pos.Wert()))
            return False
        # final, alle Tests bestanden, return true :-)
        __logger.debug("Shelly,         {name:16s}, Bereit_Oben erfolgreich".format(name=self._Name))
        return True

    def Fahre_Weitgehend_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo weitgehend (20% Rest) zu zu fahren
        """
        if __mqttclient.publish(self._Pattern + "/roller/0/command/pos", "40"):
            __logger.info(" Shelly,         {name:16s},  Rollo wird auf 40% gefahren".format(name=self._Name))
        else:
            __logger.error("Shelly,         {name:16s},  mqtt message failed".format(name=self._Name))

    def Fahre_Zu(self):
        """Shelly:Fahre_Weitgehend_Zu
        Gibt das Kommando, den Rollo zu schliessen
        """
        if __mqttclient.publish(self._Pattern + "/roller/0/command", "close"):
            __logger.info(" Shelly,         {name:16s},   Rollo wird geschlossen".format(name=self._Name))
        else:
            __logger.error("Shelly,         {name:16s},   mqtt message failed".format(name=self._Name))

def on_connect(__mqttclient, userdata, flags, rc):
    __logger.info(" on_connect,                     , Connected with result code " + str(rc))
    for i in __Mqtt_Topic_Liste:
        i.Subscribe()

def on_message(__mqttclient, userdata, msg):
    for i in __Mqtt_Topic_Liste:
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
                      format='%(levelname)s, [%(threadName)s], %(message)s')

    global __logger
    __logger = logging.getLogger()

    global __Status_Helligkeit
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//H">34.1</MqttMessage>
    __Status_Helligkeit = MqttHelligkeit("Sensor/WZTuF/EG/WZ//H", "Aussen-Hellig")
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//T">34.1</MqttMessage>
    _Status_Temperatur = MqttTemperatur("Sensor/WZTuF/EG/WZ//T", "Aussen-Temp   ")

    global __Shelly_SZ
    __Shelly_SZ = Shelly("shellies/shellyswitch25-745815", "SZ", "192.168.2.49")

    global __mqttclient
    __mqttclient = mqtt.Client()
    __mqttclient.on_connect = on_connect
    __mqttclient.on_message = on_message

    __mqttclient.connect("diskstation.fritz.box", 1883, 60)

    __mqttclient.loop_forever()

# das sind die globalen Variablen..
# Für die Callbacks von mqtt und MqttNachricht zum append..
__Mqtt_Topic_Liste = []
# wird von MqttTemperatur genutzt:
__Status_Helligkeit = []
__Shelly_SZ = []
# globales logging
__logger = []
# mqtt client (MqttNachricht: zum subscribe, Shelly: zum publish)
__mqttclient = []

if __name__ == '__main__':
    main()
