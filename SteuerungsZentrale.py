#!/usr/bin/env python
# -*- coding: cp1252 -*-
"""SteuerungsZentrale
Dieses Modul steuert die IOT-Geraete bei uns im Haus.
Es registiert sich auf MQTT-Pattern und triggert basierend auf deren Werte Aktionen
"""
from __future__ import print_function

import os
import logging
import optparse
import time
import urllib2
import threading

import paho.mqtt.client as mqtt

# Debug Level: Liste mit Klassennamen, die print_alles-Methode prueft, ob der Name in der Liste ist
# Falls ja: print, falls nein: nix
DEBUG_KLASSE = ["MqttNachricht", "MqttTemperatur", "MqttHelligkeit", "MqttShelly", "Shelly"]
LOGGING_LEVELS = {'critical': logging.CRITICAL,
                  'error': logging.ERROR,
                  'warning': logging.WARNING,
                  'info': logging.INFO,
                  'debug': logging.DEBUG}

# das sind die globalen Variablen..
# Für die Callbacks von mqtt und MqttNachricht zum append..
_Mqtt_Topic_Liste = []
# wird von MqttTemperatur genutzt:
_Status_Helligkeit = []
_Status_Temperatur = []
_Shelly_SZ = []
# globales logging
_logger = []
# mqtt client (MqttNachricht: zum subscribe, Shelly: zum publish)
_mqttclient = []



class MqttNachricht: # pylint: disable=old-style-class
    """MqttNachricht
    Die generelle Klasse fuer Mqtt-Nachrichten.
    Im Konstrutkor bekommt sie einen Namen, ein Pattern und einen Timeout
    Sie registriert sich auf das MQTT-Pattern.
    Wenn ein Wert kommt, merkt sie sich, wann er gekommen ist.
    Nach "Timeout" wird der Wert als ungueltig gewertet.
    Da die Quellen fuer die Nachrichten unterschiedlich sind, sollten abgeleitete Klassen eigene
    "gueltig" Implementierungen haben.
    """

    def __init__(self, pattern, name, timeout):
        """__init__
        Initialisierung der MqttNachricht-Basisklasse"""
        self.m_zeitpunkt = 0     # Variabel, Zugriff muss Threadsafe sein
        self.m_wert = ""         # Variabel, Zugriff muss Threadsafe sein
        self.m_pattern = pattern # Konstant
        self.m_name = name       # Konstant
        self.m_timeout = timeout # Konstant
        self.m_gueltig_event = threading.Event()
        self.m_lock = threading.RLock()
        global _Mqtt_Topic_Liste # pylint: disable=global-statement
        _Mqtt_Topic_Liste.append(self)
        for i in DEBUG_KLASSE:
            if i == self.__class__.__name__:
                _logger.debug("MqttNachricht,  {name:16s}, neues Pattern {pattern:s}".format(
                    name=self.m_name,
                    pattern=self.m_pattern))

    def gueltig(self):
        """gueltig
        Basis-Implementierung: ist ein Wert neuer als Timeout vorhanden?"""
        self.m_lock.acquire()
        zeitpunkt = self.m_zeitpunkt
        self.m_lock.release()
        if time.time() - zeitpunkt < self.m_timeout:
            return True
        self.m_gueltig_event.clear()
        return False

    def update(self, pattern, nachricht):
        """update
        Basis-Implementierung: Wenn das Pattern passt, merke den neuen Wert und die Zeit"""
        if self.m_pattern == pattern:
            self.m_lock.acquire()
            self.m_zeitpunkt = time.time()
            self.m_wert = nachricht
            self.m_lock.release()
            self.m_gueltig_event.set()
            self.print_alles()
            return True
        return False

    def wert(self):
        """wert
        Thread-gesicherte Rueckgabe des Wertes der letzten Nachricht"""
        self.m_lock.acquire()
        local_wert = self.m_wert
        self.m_lock.release()
        return local_wert

    def zeit(self):
        """zeit
        Thread-gesicherte Rueckgabe des Zeitpunkts der letzten Nachricht"""
        self.m_lock.acquire()
        local_zeit = self.m_zeitpunkt
        self.m_lock.release()
        return local_zeit

    def print_alles(self):
        """print_alles
        Debug-Output"""
        for i in DEBUG_KLASSE:
            if i == self.__class__.__name__:
                self.m_lock.acquire()
                _logger.debug("MqttNachricht, {n:16s}, t: {z:8s}, w: {w:8s}, {g:3s}".format(
                    n=self.m_name,
                    z=time.strftime("%X", time.localtime(self.m_zeitpunkt)),
                    w=self.m_wert,
                    g=" ok" if self.gueltig() else "alt"))
                self.m_lock.release()
                return

    def subscribe(self):
        """subscribe
        Registriert das eigene Topic beim Mqtt-Server"""
        if not _mqttclient.subscribe(self.m_pattern):
            _logger.error("MqttNachricht,  {name:16s}, Fehler bei subscribe to {s:s}".format(
                name=self.m_name,
                s=self.m_pattern))
        else:
            _logger.debug("MqttNachricht,  {name:16s}, subscribed to {s:s}".format(
                name=self.m_name,
                s=self.m_pattern))

class MqttSensor(MqttNachricht):
    """Zwischenklasse
    fuer Werte, die von dem Sensor kommen, gibt es eine Default-gueltig-Implementierung"""
    def gueltig(self):
        if MqttNachricht.gueltig(self):
            return True
        _logger.debug("MqttSensor, {name:16s}, Noch kein gueltiger Wert, warte auf einen".format(
            name=self.m_name))
        self.m_gueltig_event.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
        if MqttNachricht.gueltig(self):
            _logger.debug("MqttSensor, {name:16s}, Jetzt gueltiger Wert".format(
                name=self.m_name))
            return True
        _logger.warning("MqttSensor, {name:16s}, Immer noch kein gueltiger Wert".format(
            name=self.m_name))
        return False

class MqttTemperatur(MqttSensor):
    """MqttTemperatur
    Ist eine Spezialisierung von MqttNachricht.
    In der update-Methode wird geprueft, ob der Rollo zu bewegen ist.
    """

    def __init__(self, pattern, name, timeout):
        self.m_aktivitaetstimer = 0 # Variabel, Zugriff muss Threadsafe sein
        # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz
#        MqttNachricht.__init__(self, pattern, name, 100)
        MqttSensor.__init__(self, pattern, name, timeout)

    def temperatur_check(self):
        """erster Schritt: die Funktion wird in einem eigenen Thread gestartet"""
        _logger.debug("In neuem Thread")
        if not _Status_Helligkeit.gueltig():
            _logger.warning("MqttTemperatur, {name:16s}, Kein Helligkeitswert".format(
                name=self.m_name))
            return
        helligkeit = int(_Status_Helligkeit.wert())
        if helligkeit < 3000:
            _logger.debug("MqttTemperatur, {name:16s}, zu dunkel h: {hell:d}".format(
                name=self.m_name,
                hell=helligkeit))
            return
        if _Shelly_SZ.automatic_mode_oben():
            _logger.debug(
                "MqttTemperatur, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                    name=self.m_name))
            for i in _Mqtt_Topic_Liste:
                i.print_alles()
            _Shelly_SZ.schliesse_teilweise()
            self.m_lock.acquire()
            self.m_aktivitaetstimer = time.time()
            self.m_lock.release()
        else:
            _logger.debug("MqttTemperatur, {name:16s}, Shelly nicht bereit".format(
                name=self.m_name))


    def update(self, pattern, nachricht):
        """MqttTemperatur.update
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
        if MqttNachricht.update(self, pattern, nachricht):
            self.m_lock.acquire()
            temperatur = float(self.m_wert)
            aktiv_timeout = time.time() - self.m_aktivitaetstimer
            self.m_lock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            _logger.debug("MqttTemperatur, {name:16s}, T: {t:f} stunde: {s:d} L_A: {a:f}".format(
                name=self.m_name, t=temperatur, s=stunde, a=aktiv_timeout))
            # Ist es heiss genug, passt die Zeit und wurde Shelly eine Stunde nicht verfahren
            if (temperatur > 27.0) and (stunde > 10) and (stunde < 20) and (aktiv_timeout > 3600):
                _logger.debug("MqttTemperatur, {name:16s}, Temperatur und Zeit passt".format(
                    name=self.m_name))
                _logger.debug("Starte neuen Thread")
                threading.Thread(target=self.temperatur_check).start()

class MqttHelligkeit(MqttSensor):
    """MqttHelligkeit
    Ist eine Spezialisierung von MqttNachricht.
    In der update-Methode wird geprueft, ob der Rollo zu bewegen ist
    """

    def __init__(self, pattern, name, timeout):
        self.m_aktivitaetstimer = 0
        # der Sensor schickt seine Werte schnell hintereinander, daher ist der Timeout recht kurz
#        MqttNachricht.__init__(self, pattern, name, 100)
        MqttSensor.__init__(self, pattern, name, timeout)

    def helligkeit_check(self):
        """erster Schritt: die Funktion wird in einem eigenen Thread gestartet"""
        _logger.debug("In neuem Thread")
        if _Shelly_SZ.automatic_mode_oben():
            _logger.debug(
                "MqttHelligkeit, {name:16s}, Alle Bedingungen passen - fahre Rollo runter".format(
                    name=self.m_name))
            for i in _Mqtt_Topic_Liste:
                i.print_alles()
            _Shelly_SZ.schliesse_komplett()
            self.m_lock.acquire()
            self.m_aktivitaetstimer = time.time()
            self.m_lock.release()
        else:
            _logger.debug("MqttHelligkeit, {name:16s}, Shelly nicht bereit".format(
                name=self.m_name))

    def update(self, pattern, nachricht):
        """MqttHelligkeit.update
        Hier wird geprueft, ob die Bedingungen erfuellt sind, den Rollo runterzufahren.
        - Sind alle anderen benoetigten Werte da und aktuell?
        - Daemmert es, d.h. die Helligkeit ist ueber einer Schwelle und unter einer anderen
        - Ist es frueh am Tag (runterfahren, wenn das Fenster nachts auf war)
        - Sind die Rollo-Schalter auf "aus" (sonst keine Automatik)
        - Der Rollo laeuft nicht
        - Der Rollo ist oben (Position 0)
        """
        # Erster Schritt: Basis-Klasse aufrufen
        if MqttNachricht.update(self, pattern, nachricht):
            self.m_lock.acquire()
            helligkeit = int(self.m_wert)
            aktiv_timeout = time.time() - self.m_aktivitaetstimer
            self.m_lock.release()
            zeit = time.localtime()
            stunde = zeit.tm_hour
            _logger.debug("MqttHelligkeit, {name:16s}, H: {h:d} stunde: {s:d} L_A: {a:f}".format(
                name=self.m_name, h=helligkeit, s=stunde, a=aktiv_timeout))
            # Ist es hell genug, passt die Zeit und wurde Shelly eine Stunde nicht verfahren
            if ((helligkeit > 50) and (helligkeit < 500) and (stunde > 3) and (stunde < 6) and
                    (aktiv_timeout > 3600)):
                _logger.debug("MqttHelligkeit, {n:16s}, Helligkeitsbereich und Zeit passt".format(
                    n=self.m_name))
                _logger.debug("Starte neuen Thread")
                threading.Thread(target=self.helligkeit_check).start()


class MqttShelly(MqttNachricht):
    """Zwischenklasse:
    fuer Werte, die von einem Shelly kommen, gibt es eine Default-gueltig-Implementierung"""
    def __init__(self, pattern, name, timeout, shelly):
        self.m_shelly = shelly
        MqttNachricht.__init__(self, pattern, name, timeout)


    def gueltig(self):
        if MqttNachricht.gueltig(self):
            return True
        _logger.debug("MqttShelly, {name:16s}, Noch kein gueltiger Wert, fordere neuen an".format(
            name=self.m_name))
        self.m_shelly.trigger_mqtt()
        self.m_gueltig_event.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
        if MqttNachricht.gueltig(self):
            _logger.debug("MqttShelly, {name:16s}, Jetzt gueltiger Wert".format(
                name=self.m_name))
            return True
        _logger.warning("MqttShelly, {name:16s}, Immer noch kein gueltiger Wert".format(
            name=self.m_name))
        return False

class Shelly: # pylint: disable=old-style-class
    """Shelly
    Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln
    """
    def __init__(self, pattern, name, ip_addr):
        self.m_name = name
        self.m_pattern = pattern
        self.m_ip = ip_addr
        # Werte bleiben 5 Minuten gueltig
        self.m_schalter_0 = MqttShelly(pattern + "/input/0", name + "-Schalter 0", 300, self)
        self.m_schalter_1 = MqttShelly(pattern + "/input/1", name + "-Schalter 1", 300, self)
        self.m_rollo = MqttShelly(pattern + "/roller/0", name + "-Rollo Stat", 300, self)
        self.m_rollo_pos = MqttShelly(pattern + "/roller/0/pos", name + "-Rollo Pos ", 300, self)
        self.print_alles()

    def print_alles(self):
        """print_alles
        Debug-Output..."""
        for i in DEBUG_KLASSE:
            if i == self.__class__.__name__:
                _logger.debug("Shelly, {name:16s}, Basis-Pattern {wert:s}".format(
                    name=self.m_name,
                    wert=self.m_pattern))
                return

    def trigger_mqtt(self):
        """Shelly:trigger_mqtt
        Falls kein gueltiger Mqtt-Wert da ist, kann man einen Trigger setzen, um einen neuen
        Wert zu bekommen. Das wird per http-Request gemacht: mqtt-Timeout auf 1 (s) setzen,
        dann wieder auf 0 (d.h. nie).
        In der nächsten Schleife sollten dann gueltige Werte da sein.
        """
        _logger.debug("Shelly, {name:16s}, trigger_mqtt".format(name=self.m_name))
        urllib2.urlopen("http://"+self.m_ip + "/settings?mqtt_update_period=2")
        time.sleep(2)
        urllib2.urlopen("http://"+self.m_ip + "/settings?mqtt_update_period=0")

    def automatic_mode_oben(self):
        """Shelly:Bereit
        Hier wird geprueft, ob der Shelly aktuelle Werte hat, die Schalter auf 0 stehen
        und kein Motor laeuft. In dem Fall ist 'Automatik-Mode' an.
        """
        if not self.m_schalter_0.gueltig():
            _logger.warning("Shelly, {name:16s}, Schalter(0) kein aktueller Wert".format(
                name=self.m_name))
            return False
        if self.m_schalter_0.wert() != "0":
            _logger.debug("Shelly, {name:16s}, Schalter(0) nicht aus, Wert: {wert:s}".format(
                name=self.m_name, wert=self.m_schalter_0.wert()))
            return False
        if not self.m_schalter_1.gueltig():
            _logger.warning("Shelly, {name:16s}, Schalter(1) kein aktueller Wert".format(
                name=self.m_name))
            return False
        if self.m_schalter_1.wert() != "0":
            _logger.debug("Shelly, {name:16s}, Schalter(1) nicht aus, Wert: {wert:s}".format(
                name=self.m_name, wert=self.m_schalter_1.wert()))
            return False
        if not self.m_rollo.gueltig():
            _logger.warning("Shelly, {name:16s}, Rollo kein aktueller Wert".format(
                name=self.m_name))
            return False
        if self.m_rollo.wert() != "stop":
            _logger.debug("Shelly, {name:16s}, Rollo laeuft, Wert: {wert:s}".format(
                name=self.m_name, wert=self.m_rollo.wert()))
            return False
        if not self.m_rollo_pos.gueltig():
            _logger.warning("Shelly, {name:16s}, Rollo_Pos kein aktueller Wert".format(
                name=self.m_name))
            return False
        if self.m_rollo_pos.wert() != "100":
            _logger.debug("Shelly, {name:16s}, Rollo Position nicht oben, Wert: {wert:s}".format(
                name=self.m_name, wert=self.m_rollo_pos.wert()))
            return False
        # final, alle Tests bestanden, return true :-)
        _logger.debug("Shelly, {name:16s}, automatic_mode_oben ok".format(name=self.m_name))
        return True

    def schliesse_teilweise(self):
        """Shelly:schliesse_teilweise
        Gibt das Kommando, den Rollo weitgehend (20% Rest) zu zu fahren
        """
        if _mqttclient.publish(self.m_pattern + "/roller/0/command/pos", "40"):
            _logger.info(" Shelly, {name:16s}, Rollo faehrt auf 40%".format(name=self.m_name))
        else:
            _logger.error("Shelly, {name:16s}, mqtt message failed".format(name=self.m_name))

    def schliesse_komplett(self):
        """Shelly:schliesse_komplett
        Gibt das Kommando, den Rollo zu schliessen
        """
        if _mqttclient.publish(self.m_pattern + "/roller/0/command", "close"):
            _logger.info(" Shelly, {name:16s}, Rollo wird geschlossen".format(name=self.m_name))
        else:
            _logger.error("Shelly,{name:16s}, mqtt message failed".format(name=self.m_name))

def on_connect(client, userdata, flags, result):
    """on_connect
    registiert alle Topics beim MQTT-Server"""
    del client, userdata, flags # nicht benuetzt
    _logger.info(" on_connect, , Connected with result code {r_code:d}".format(
        r_code=result))
    for i in _Mqtt_Topic_Liste:
        i.subscribe()

def on_message(client, userdata, msg):
    """on_message
    MQTT-Callback wenn eine Nachricht ankommt"""
    del client, userdata # nicht benuetzt
    for i in _Mqtt_Topic_Liste:
        if i.update(msg.topic, msg.payload):
            break

def main():
    """main:
    Einstiegspunkt"""
    # missing timezone info when running in docker
    os.environ['TZ'] = 'Europe/Berlin'

    parser = optparse.OptionParser()
    parser.add_option('-l', '--logging-level', help='Logging level')
    parser.add_option('-f', '--logging-file', help='Logging file name')
    (options, args) = parser.parse_args()
    del args # wird nicht benutzt
    logging_level = LOGGING_LEVELS.get(options.logging_level, logging.NOTSET)
    logging.basicConfig(level=logging_level, filename=options.logging_file,
                        format='%(levelname)s, [%(threadName)s], %(message)s')

    global _logger # pylint: disable=global-statement
    _logger = logging.getLogger()

    global _Status_Helligkeit # pylint: disable=global-statement
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//H">34.1</MqttMessage>
    _Status_Helligkeit = MqttHelligkeit("Sensor/WZTuF/EG/WZ//H", "Aussen-Hellig", 100)
    global _Status_Temperatur # pylint: disable=global-statement
    #<MqttMessage topic="Sensor/WZTuF/EG/WZ//T">34.1</MqttMessage>
    _Status_Temperatur = MqttTemperatur("Sensor/WZTuF/EG/WZ//T", "Aussen-Temp   ", 100)

    global _Shelly_SZ # pylint: disable=global-statement
    _Shelly_SZ = Shelly("shellies/shellyswitch25-745815", "SZ", "192.168.2.49")

    global _mqttclient # pylint: disable=global-statement
    _mqttclient = mqtt.Client()
    _mqttclient.on_connect = on_connect
    _mqttclient.on_message = on_message

    _mqttclient.connect("diskstation.fritz.box", 1883, 60)

    _mqttclient.loop_forever()

if __name__ == '__main__':
    main()
