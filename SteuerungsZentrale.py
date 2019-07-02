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
import Queue

import paho.mqtt.client as mqtt

# Debug Level: Liste mit Klassennamen, die print_alles-Methode prueft, ob der Name in der Liste ist
# Falls ja: print, falls nein: nix
DEBUG_KLASSE = ["MqttNachricht", "MqttTemperatur", "MqttHelligkeit", "MqttShelly", "Shelly"]
LOGGING_LEVELS = {'critical': logging.CRITICAL,
                  'error': logging.ERROR,
                  'warning': logging.WARNING,
                  'info': logging.INFO,
                  'debug': logging.DEBUG}

class AktivitaetsQueue(object):
    """AktivitaetsQueue
    Singleton-Klasse: startet und verwaltet eine (einzige) Queue"""
    _thread = None
    _queue = None
    _weiter = False

    @classmethod
    def _mqtt_exit(cls):
        _LogSup.log().debug("AktivitaetsQueue, exite Thread")
        if not cls._thread is None:
            _LogSup.log().debug("...")
            del cls._queue
            cls._queue = None
            cls._thread.join()
            del cls._thread
            cls._thread = None
        cls._weiter = False

    @classmethod
    def _queue_worker(cls):
        while cls._weiter:
            try:
                item = cls._queue.get(block=True, timeout=5)
                item.trigger_intern()
                cls._queue.task_done()
            except Queue.Empty:
                pass
        _LogSup.log().warning("AktivitaetsQueue, Queue angehalten")
        cls._mqtt_exit()

    @classmethod
    def start_queue(cls):
        _LogSup.log().debug("AktivitaetsQueue, starte Queue")
        if cls._thread is None:
            cls._weiter = True
            cls._queue = Queue.Queue()
            cls._thread = threading.Thread(target=cls._queue_worker)
            cls._thread.start()

    @classmethod
    def stop_queue(cls):
        _LogSup.log().debug("AktivitaetsQueue, stoppe Queue")
        cls._weiter = False

    @classmethod
    def put(cls, msg):
        _LogSup.log().debug("AktivitaetsQueue, neue Nachricht")
        cls._queue.put(msg)


class Aktivitaet(object):
    """Aktivitaet
    Mqtt-Nachrichten werden von allen Aktivitaeten (Aktivitaet-Objekte) geprueft, ob ggfs.
    etwas zu tun waere. Falls ja, schiebt die Aktivitaet sich eine Nachricht in die Queue
    und arbeitet sie in den Aktivitaets-Thread ab.
    Dadurch werden die Aktivitaeten auf jeden Fall serialisiert und kommen sich nicht
    in die Quere.

    Als erste Version bekommt jede "Art" von Aktivitaet eine eigene abgeleitete Klasse.
    In dieser Klasse werden die eigentlichen Bedingungen und Aktionen hinterlegt.
    TODO: Umstellen auf ein mehr Parametrisiertes Vorgehen (neue Aktivitaet durch
    Konfiguration anstatt Programmierung)
    """

    def __init__(self):
        AktivitaetsQueue.start_queue()
        self.m_lock = threading.RLock()
        _LogSup.log().debug("Aktivitaet,  {name:16s}, __init__".format(
            name=self))


    def trigger(self):
        """Aktivitaet:trigger
        Prueft, ob ggfs. etwas zu tun waere - Notwendige Bedingungen.
        Darf nur schnell pruefen und nicht blockieren.
        Falls der notwendige Check positiv ist, stellt trigger eine Nachricht
        in die Queue. In einem Worker-Thread wird dann der 2. Teil gemacht.
        Siehe trigger_intern"""
        AktivitaetsQueue.put(self)

    def trigger_intern(self):
        """Aktivitaet:trigger_intern
        Im 2. Teil werden die notwendigen Bedingungen geprueft.
        Falls diese positiv sind, wird die Aktivitaet aufgerufen"""
        _LogSup.log().debug("Aktivitaet,  {name:16s}, trigger_intern".format(
            name=self))


class AktivitaetSchattenbeiHitze(Aktivitaet):
    """AktivitaetSchattenbeiHitze
    Bedingungen:
    Notwendig:
        Zeit:  10:00 - 19:59
        Temperatur: > 27.0
        keine Aktivitaet seit: 3600s
    Hinreichend:
        Helligkeit: >= 3000
        Shelly in Automatic-Mode
        Rollo oben
    """

    def __init__(self, name, temp_sensor, hell_sensor, shelly):
        Aktivitaet.__init__(self)
        self.m_name = name
        self.m_temp_sensor = temp_sensor
        self.m_hell_sensor = hell_sensor
        self.m_shelly = shelly
        self.m_aktivitaetstimer = 0 # Zugriff muss Threadsafe sein

    def trigger(self):
        """AktivitaetSchattenbeiHitze:trigger
        Pruefen der notwendigen Bedingungen (1. Schritt):
        Ist es heiss genug, passt die Zeit und wurde Shelly eine Stunde nicht verfahren?

        Noch im Main-Thread"""
        zeit = time.localtime()
        stunde = zeit.tm_hour
        if (stunde > 9) and (stunde < 20):
            self.m_lock.acquire()
            aktiv_timeout = time.time() - self.m_aktivitaetstimer
            self.m_lock.release()
            if aktiv_timeout > 3600:
                if self.m_temp_sensor.gueltig():
                    temperatur = float(self.m_temp_sensor.wert())
                    if temperatur > 27.0:
                        _LogSup.log().debug(
                            "A_SchattenbeiHitze, {n:16s}, T und Zeit passt".format(
                                n=self.m_name))
                        Aktivitaet.trigger(self)

    def trigger_intern(self):
        """AktivitaetSchattenbeiHitze:trigger_intern
        Pruefen der hinreichenden Bedingungen (2. Schritt)
        Ist es hell genug, ist der Shelly im Auto-Mode und ist der Rollo oben?
        Falls ja: Fahre Rollo teilweise zu.

        Jetzt im Aktivitaets-Thread"""
        if not self.m_hell_sensor.gueltig():
            _LogSup.log().warning("A_SchattenbeiHitze, {n:16s}, Keine Helligkeit".format(
                n=self.m_name))
            return
        helligkeit = int(self.m_hell_sensor.wert())
        if helligkeit < 3000:
            _LogSup.log().debug("A_SchattenbeiHitze, {n:16s}, zu dunkel h: {h:d}".format(
                n=self.m_name,
                h=helligkeit))
            return
        if self.m_shelly.automatic_mode():
            if self.m_shelly.position_oben():
                _LogSup.log().info(
                    "A_SchattenbeiHitze, {n:16s}, fahre auf 40% um {z:8s} T={t:f} H={h:d}".format(
                        n=self.m_name,
                        z=time.strftime("%X"),
                        t=float(self.m_temp_sensor.wert()),
                        h=helligkeit))
                self.m_shelly.schliesse_teilweise()
                self.m_lock.acquire()
                self.m_aktivitaetstimer = time.time()
                self.m_lock.release()
            else:
                _LogSup.log().debug(
                    "A_SchattenbeiHitze, {name:16s}, Shelly Rollo nicht oben".format(
                        name=self.m_name))
        else:
            _LogSup.log().debug(
                "A_SchattenbeiHitze, {name:16s}, Shelly nicht im Auto-Mode".format(
                    name=self.m_name))


class AktivitaetNachtsRolloSchliessen(Aktivitaet):
    """AktivitaetNachtsRolloSchliessen
    Bedingungen:
    Notwendig:
        Zeit:  4:00 - 5:59
        Helligkeit: 51 - 999
        keine Aktivitaet seit: 3600s
    Hinreichend:
        Shelly in Automatic-Mode
        Rollo oben
    """

    def __init__(self, name, hell_sensor, shelly):
        Aktivitaet.__init__(self)
        self.m_name = name
        self.m_hell_sensor = hell_sensor
        self.m_shelly = shelly
        self.m_aktivitaetstimer = 0 # Zugriff muss Threadsafe sein

    def trigger(self):
        """AktivitaetNachtsRolloSchliessen:trigger
        Pruefen der notwendigen Bedingungen (1. Schritt):
        Ist es heiss genug, passt die Zeit und wurde Shelly eine Stunde nicht verfahren?

        Noch im Main-Thread"""
        zeit = time.localtime()
        stunde = zeit.tm_hour
        if (stunde > 3) and (stunde < 6):
            self.m_lock.acquire()
            aktiv_timeout = time.time() - self.m_aktivitaetstimer
            self.m_lock.release()
            if aktiv_timeout > 3600:
                if self.m_hell_sensor.gueltig():
                    helligkeit = int(self.m_hell_sensor.wert())
                    if (helligkeit > 50) and (helligkeit < 1000):
                        _LogSup.log().debug(
                            "A_NachtsRolloSchliessen, {n:16s}, Helligkeit und Zeit passt".format(
                                n=self.m_name))
                        Aktivitaet.trigger(self)

    def trigger_intern(self):
        """AktivitaetNachtsRolloSchliessen:trigger_intern
        Pruefen der hinreichenden Bedingungen (2. Schritt)
        Ist es hell genug, ist der Shelly im Auto-Mode und ist der Rollo oben?
        Falls ja: Fahre Rollo teilweise zu.

        Jetzt im Aktivitaets-Thread"""
        if self.m_shelly.automatic_mode():
            if self.m_shelly.position_oben():
                _LogSup.log().info(
                    "A_NachtsRolloSchliessen, {n:16s}, schliesse Rollo um {z:8s} H={h:d}".format(
                        n=self.m_name,
                        z=time.strftime("%X"),
                        h=int(self.m_hell_sensor.wert())))
                self.m_shelly.schliesse_teilweise()
                self.m_lock.acquire()
                self.m_aktivitaetstimer = time.time()
                self.m_lock.release()
            else:
                _LogSup.log().debug(
                    "A_NachtsRolloSchliessen, {n:16s}, Shelly Rollo nicht oben".format(
                        n=self.m_name))
        else:
            _LogSup.log().debug(
                "A_NachtsRolloSchliessen, {n:16s}, Shelly nicht im Auto-Mode".format(
                    n=self.m_name))


class MqttNachricht(object):
    """MqttNachricht
    Die generelle Klasse fuer Mqtt-Nachrichten.
    Im Konstrutkor bekommt sie einen Namen, ein Pattern und einen Timeout
    Sie registriert sich auf das MQTT-Pattern.
    Wenn ein Wert kommt, merkt sie sich, wann er gekommen ist.
    Nach "Timeout" wird der Wert als ungueltig gewertet.
    Da die Quellen fuer die Nachrichten unterschiedlich sind, sollten abgeleitete Klassen eigene
    "gueltig" Implementierungen haben.

    MqttNachricht kann mit Aktivitaeten angereichert werden.
    Wenn eine passende Nachricht kommt, werden die Aktivitaeten getriggert
    """
    # pylint: disable=too-many-instance-attributes

    def __init__(self, pattern, name, timeout):
        """__init__
        Initialisierung der MqttNachricht-Basisklasse"""
        self.m_zeitpunkt = 0 # Variabel, Zugriff muss Threadsafe sein
        self.m_wert = "" # Variabel, Zugriff muss Threadsafe sein
        self.m_pattern = pattern # Konstant
        self.m_name = name # Konstant
        self.m_timeout = timeout # Konstant
        self.m_gueltig_event = threading.Event()
        self.m_lock = threading.RLock()
        self.m_aktivitaeten = []
        MqttSup.register(self)
        for i in DEBUG_KLASSE:
            if i == self.__class__.__name__:
                _LogSup.log().debug("MqttNachricht,  {name:16s}, Pattern {pattern:s}".format(
                    name=self.m_name,
                    pattern=self.m_pattern))

    def plus_aktivitaet(self, aktivitaet):
        """plus_aktivitaet
        Haengt eine (weiter) aktivitaet in die interne Liste ein"""
        self.m_aktivitaeten.append(aktivitaet)

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
            _LogSup.log().debug("MqttNachricht, update {n:16s}, {w:s}".format(
                n=self.m_name,
                w=nachricht))
            self.m_lock.acquire()
            self.m_zeitpunkt = time.time()
            self.m_wert = nachricht
            self.m_lock.release()
            self.m_gueltig_event.set()
            self.print_alles()
            for i in self.m_aktivitaeten:
                i.trigger()
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
                _LogSup.log().debug("MqttNachricht, {n:16s}, t: {z:8s}, w: {w:8s}, {g:3s}".format(
                    n=self.m_name,
                    z=time.strftime("%X", time.localtime(self.m_zeitpunkt)),
                    w=self.m_wert,
                    g=" ok" if self.gueltig() else "alt"))
                self.m_lock.release()
                return

    def subscribe(self):
        """subscribe
        Registriert das eigene Topic beim Mqtt-Server"""
        if not MqttSup.subscribe(self.m_pattern): # _mqttclient
            _LogSup.log().error("MqttNachricht,  {name:16s}, Fehler bei subscribe to {s:s}".format(
                name=self.m_name,
                s=self.m_pattern))
        else:
            _LogSup.log().debug("MqttNachricht,  {name:16s}, subscribed to {s:s}".format(
                name=self.m_name,
                s=self.m_pattern))


class MqttSensor(MqttNachricht):
    """Zwischenklasse
    fuer Werte, die von dem Sensor kommen, gibt es eine Default-gueltig-Implementierung"""

    def gueltig(self):
        if MqttNachricht.gueltig(self):
            return True
        if not MqttSup.darf_warten():
            return False # im Mainthread nicht warten !
        _LogSup.log().debug(
            "MqttSensor, {name:16s}, Noch kein gueltiger Wert, warte auf einen".format(
                name=self.m_name))
        self.m_gueltig_event.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
        if MqttNachricht.gueltig(self):
            _LogSup.log().debug("MqttSensor, {name:16s}, Jetzt gueltiger Wert".format(
                name=self.m_name))
            return True
        _LogSup.log().warning("MqttSensor, {name:16s}, Immer noch kein gueltiger Wert".format(
            name=self.m_name))
        return False

class MqttShelly(MqttNachricht):
    """Zwischenklasse:
    fuer Werte, die von einem Shelly kommen, gibt es eine Default-gueltig-Implementierung"""

    def __init__(self, pattern, name, timeout, shelly):
        self.m_shelly = shelly
        MqttNachricht.__init__(self, pattern, name, timeout)

    def gueltig(self):
        if MqttNachricht.gueltig(self):
            return True
        _LogSup.log().debug(
            "MqttShelly, {name:16s}, Noch kein gueltiger Wert, fordere neuen an".format(
                name=self.m_name))
        self.m_shelly.trigger_mqtt()
        if not MqttSup.darf_warten():
            return False # im Mainthread nicht warten !
        self.m_gueltig_event.wait(5) # sollte noch kein Wert da sein, max. 5 Sekunden warten
        if MqttNachricht.gueltig(self):
            _LogSup.log().debug(
                "MqttShelly, {name:16s}, Jetzt gueltiger Wert".format(
                    name=self.m_name))
            return True
        _LogSup.log().warning(
            "MqttShelly, {name:16s}, Immer noch kein gueltiger Wert".format(
                name=self.m_name))
        return False


class Shelly(object):
    """Shelly
    Die Shelly Klasse behandelt ein Shelly 2.5, d.h. kann den Status und die Befehle buendeln
    """
    # pylint: disable=too-many-instance-attributes
    # Zumindest aktuell sind die Attribute noetig.
    # Vielleicht ein TODO fuer spaeter

    def __init__(self, pattern, name, ip_addr):
        self.m_name = name
        self.m_pattern = pattern
        self.m_ip = ip_addr
        # Werte bleiben 1 Stunde gueltig
        self.m_schalter_0 = MqttShelly(pattern + "/input/0", name + "-Schalter 0", 3600, self)
        self.m_schalter_1 = MqttShelly(pattern + "/input/1", name + "-Schalter 1", 3600, self)
        self.m_rollo = MqttShelly(pattern + "/roller/0", name + "-Rollo Stat", 3600, self)
        self.m_rollo_pos = MqttShelly(pattern + "/roller/0/pos", name + "-Rollo Pos ", 3600, self)
        self.m_rollo_command = MqttNachricht(pattern + "/roller/0/command",
                                             name + "-Rollo Command", 1)
        self.m_rollo_command_pos = MqttNachricht(pattern + "/roller/0/command/pos",
                                                 name + "-Rollo Command Pos ", 1)
        self.print_alles()

    def print_alles(self):
        """print_alles
        Debug-Output..."""
        for i in DEBUG_KLASSE:
            if i == self.__class__.__name__:
                _LogSup.log().debug("Shelly, {name:16s}, Basis-Pattern {wert:s}".format(
                    name=self.m_name,
                    wert=self.m_pattern))
                return

    def trigger_mqtt(self):
        """Shelly:trigger_mqtt
        Falls kein gueltiger Mqtt-Wert da ist, kann man einen Trigger setzen, um einen neuen
        Wert zu bekommen. Das wird per http-Request gemacht: mqtt-Timeout auf 1 (s) setzen,
        dann wieder auf 0 (d.h. nie).
        In der naechsten Schleife sollten dann gueltige Werte da sein.
        """
        _LogSup.log().debug("Shelly, {name:16s}, trigger_mqtt".format(name=self.m_name))
        try:
            urllib2.urlopen("http://"+self.m_ip + "/settings?mqtt_update_period=2")
        except urllib2.HTTPError, fehler:
            _LogSup.log().error(
                "Shelly, {name:16s}, trigger_mqtt 1 HTTPError".format(name=self.m_name))
            _LogSup.log().error(fehler)
        except urllib2.URLError, fehler:
            _LogSup.log().error(
                "Shelly, {name:16s}, trigger_mqtt 1 URLError".format(name=self.m_name))
            _LogSup.log().error(fehler)
        ###        else: ### sicherer ist das...
        for i in range(3):
            del i
            time.sleep(2)
            try:
                urllib2.urlopen("http://"+self.m_ip + "/settings?mqtt_update_period=0")
                return
            except urllib2.HTTPError, fehler:
                _LogSup.log().error(
                    "Shelly, {name:16s}, trigger_mqtt 2 HTTPError".format(name=self.m_name))
                _LogSup.log().error(fehler)
            except urllib2.URLError, fehler:
                _LogSup.log().error(
                    "Shelly, {name:16s}, trigger_mqtt 2 URLError".format(name=self.m_name))
                _LogSup.log().error(fehler)

    def automatic_mode(self):
        """Shelly:automatic_mode_oben
        Hier wird geprueft, ob der Shelly aktuelle Werte hat, die Schalter auf 0 stehen
        und kein Motor laeuft. In dem Fall ist 'Automatik-Mode' an.
        """
        # pylint: disable=too-many-return-statements
        # Der Code ist so uebersichtlicher als lauter else ...
        if not self.m_schalter_0.gueltig():
            _LogSup.log().warning(
                "Shelly, {name:16s}, Schalter(0) kein aktueller Wert".format(
                    name=self.m_name))
            return False
        if self.m_schalter_0.wert() != "0":
            _LogSup.log().debug(
                "Shelly, {name:16s}, Schalter(0) nicht aus, Wert: {wert:s}".format(
                    name=self.m_name, wert=self.m_schalter_0.wert()))
            return False
        if not self.m_schalter_1.gueltig():
            _LogSup.log().warning(
                "Shelly, {name:16s}, Schalter(1) kein aktueller Wert".format(
                    name=self.m_name))
            return False
        if self.m_schalter_1.wert() != "0":
            _LogSup.log().debug(
                "Shelly, {name:16s}, Schalter(1) nicht aus, Wert: {wert:s}".format(
                    name=self.m_name, wert=self.m_schalter_1.wert()))
            return False
        if not self.m_rollo.gueltig():
            _LogSup.log().warning(
                "Shelly, {name:16s}, Rollo kein aktueller Wert".format(
                    name=self.m_name))
            return False
        if self.m_rollo.wert() != "stop":
            _LogSup.log().debug(
                "Shelly, {name:16s}, Rollo laeuft, Wert: {wert:s}".format(
                    name=self.m_name, wert=self.m_rollo.wert()))
            return False
        # final, alle Tests bestanden, return true :-)
        _LogSup.log().debug(
            "Shelly, {name:16s}, automatic_mode_oben ok".format(name=self.m_name))
        return True

    def position(self):
        """Shelly:position
        Gibt die aktuelle Position des Rollos an. 100 == oben.
        """
        if not self.m_rollo_pos.gueltig():
            _LogSup.log().warning(
                "Shelly, {name:16s}, Rollo_Pos kein aktueller Wert".format(
                    name=self.m_name))
            return False
        _LogSup.log().debug(
            "Shelly, {name:16s}, Position Rollo ist {wert:16s}".format(
                name=self.m_name, wert=self.m_rollo_pos.wert()))
        return True

    def position_oben(self):
        """Shelly:position
        Gibt die aktuelle Position des Rollos an. 100 == oben.
        """
        if not self.m_rollo_pos.gueltig():
            _LogSup.log().warning(
                "Shelly, {name:16s}, Rollo_Pos kein aktueller Wert".format(
                    name=self.m_name))
            return False
        rollo_pos = int(self.m_rollo_pos.wert())
        if rollo_pos < 96:
            _LogSup.log().debug(
                "Shelly, {name:16s}, Rollo Position nicht oben, Wert: {wert:d}".format(
                    name=self.m_name, wert=rollo_pos))
            return False
        # final, alle Tests bestanden, return true :-)
        _LogSup.log().debug(
            "Shelly, {name:16s}, Rollo ist oben".format(name=self.m_name))
        return True


    def schliesse_teilweise(self):
        """Shelly:schliesse_teilweise
        Gibt das Kommando, den Rollo weitgehend (20% Rest) zu zu fahren
        """
        if MqttSup.publish(self.m_pattern + "/roller/0/command/pos", "40"):
            _LogSup.log().info(
                " Shelly, {name:16s}, Rollo faehrt auf 40%".format(name=self.m_name))
        else:
            _LogSup.log().error(
                "Shelly, {name:16s}, mqtt message failed".format(name=self.m_name))

    def schliesse_komplett(self):
        """Shelly:schliesse_komplett
        Gibt das Kommando, den Rollo zu schliessen
        """
        ###        if MqttSup.publish(self.m_pattern + "/roller/0/command", "close"):
        ### mit pos-Kommando klappt es...
        if MqttSup.publish(self.m_pattern + "/roller/0/command/pos", "0"):
            _LogSup.log().info(
                " Shelly, {name:16s}, Rollo wird geschlossen".format(name=self.m_name))
        else:
            _LogSup.log().error(
                "Shelly,{name:16s}, mqtt message failed".format(name=self.m_name))


def on_connect(client, userdata, flags, result):
    """on_connect
    registiert alle Topics beim MQTT-Server"""
    del client, userdata, flags # nicht benuetzt
    _LogSup.log().info(" on_connect, , Connected with result code {r_code:d}".format(
        r_code=result))
    MqttSup.subscribe_all()

def on_message(client, userdata, msg):
    """on_message
    MQTT-Callback wenn eine Nachricht ankommt"""
    del client, userdata # nicht benuetzt
    MqttSup.update(msg.topic, msg.payload)


class MqttSup(object):
    """Singleton-Klasse fuer den MQTT-Support"""
    _mqttclient = None
    _consumer = []
    _loop_thread = None

    @classmethod
    def _mqtt_init(cls):
        """internal"""
        if cls._mqttclient is None:
            cls._mqttclient = mqtt.Client()
            cls._mqttclient.on_connect = on_connect
            cls._mqttclient.on_message = on_message
            cls._mqttclient.connect("diskstation.fritz.box", 1883, 60)

    @classmethod
    def loop_forever(cls):
        """internal forward to mqttclient loop_forever"""
        if cls._mqttclient is None:
            cls._mqtt_init()
        cls._loop_thread = threading.current_thread().name
        cls._mqttclient.loop_forever()
        cls._loop_thread = None

    @classmethod
    def loop_start(cls):
        """internal forward to mqttclient loop_start"""
        if cls._mqttclient is None:
            cls._mqtt_init()
        cls._loop_thread = threading.current_thread().name
        cls._mqttclient.loop_start()

    @classmethod
    def loop_stop(cls):
        """internal forward to mqttclient loop_stop"""
        cls._loop_thread = None
        cls._mqttclient.loop_stop()

    @classmethod
    def darf_warten(cls):
        """nur ausserhalb des Threads mit der "loop" darf gewartet werden"""
        if cls._loop_thread is None:
            return False
        if cls._loop_thread == threading.current_thread().name:
            return False
        return True

    @classmethod
    def subscribe(cls, pat):
        """internal forward to mqttclient subscribe"""
        if cls._mqttclient is None:
            cls._mqtt_init()
        return cls._mqttclient.subscribe(pat)

    @classmethod
    def publish(cls, pat, was):
        """internal forward to mqttclient publish"""
        if cls._mqttclient is None:
            cls._mqtt_init()
        return cls._mqttclient.publish(pat, was)

    @classmethod
    def register(cls, con):
        """Register neuen Konsumenten"""
        cls._consumer.append(con)

    @classmethod
    def update(cls, pat, nachr):
        """uebergebe Nachricht"""
        for i in cls._consumer:
            if i.update(pat, nachr):
                break

    @classmethod
    def subscribe_all(cls):
        """call consumers to subscribe"""
        for i in cls._consumer:
            i.subscribe()


class _LogSup(object):
    """Singletonklasse fuer logging"""
    # pylint: disable=too-few-public-methods
    # bewusste Singleton-Klasse, es gibt nur eine sinnvolle Methode
    _logger = None

    @classmethod
    def log(cls):
        """log(): Zugriff auf Singleton fuer Logging"""
        if cls._logger is None:
            parser = optparse.OptionParser()
            parser.add_option('-l', '--logging-level', help='Logging level')
            parser.add_option('-f', '--logging-file', help='Logging file name')
            (options, args) = parser.parse_args()
            del args # wird nicht benutzt
            logging_level = LOGGING_LEVELS.get(options.logging_level, logging.NOTSET)
            logging.basicConfig(level=logging_level, filename=options.logging_file,
                                format='%(levelname)s, [%(threadName)s], %(message)s')
            cls._logger = logging.getLogger()
        return cls._logger


def main():
    """main:
    Einstiegspunkt"""
    # missing timezone info when running in docker
    os.environ['TZ'] = 'Europe/Berlin'

    sensor_helligkeit = MqttSensor("Sensor/WZTuF/EG/WZ//H", "Aussen-Hellig", 100)

    sensor_temperatur = MqttSensor("Sensor/WZTuF/EG/WZ//T", "Aussen-Temp", 100)

    shelly_sz = Shelly("shellies/shellyswitch25-745815", "SZ", "192.168.2.49")

    aktivitaet_schatten = AktivitaetSchattenbeiHitze(
        "Schatten bei Hitze", sensor_temperatur, sensor_helligkeit, shelly_sz)
    sensor_temperatur.plus_aktivitaet(aktivitaet_schatten)

    aktivitaet_nachts = AktivitaetNachtsRolloSchliessen(
        "Nachts Rollo schliessen", sensor_helligkeit, shelly_sz)
    sensor_helligkeit.plus_aktivitaet(aktivitaet_nachts)

    try:
        MqttSup.loop_forever()
    except:
        # hier kann man nur ankommen, wenn die Loop unterbrochen wurde
        print("in Interupt")
        AktivitaetsQueue.stop_queue()


if __name__ == '__main__':
    main()
