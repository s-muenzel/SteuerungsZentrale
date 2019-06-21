#!/usr/bin/env python
# -*- coding: cp1252 -*-
"""Unittest fuer SteuerungsZentrale
Hier sollten alle wichtigen Klassen und Methoden getestet werden
"""
from __future__ import print_function
import unittest

from steuerungszentrale import MqttNachricht 
from steuerungszentrale import Shelly 
from steuerungszentrale import MqttSup 


#import os
#import logging
#import optparse
import time
#import urllib2
#import threading


class TestMqttNachricht(unittest.TestCase):
    """Unittest fuer MqttNachricht"""

#    def __init__(self, pattern, name, timeout):

    def test_gueltig(self):
        """test methode gueltig"""
        mqtt_nachricht = MqttNachricht("test", "testnachricht", 1)
        # noch kein Wert --> ungueltig
        self.assertFalse(mqtt_nachricht.gueltig(), msg="gueltig: kein Wert, trotzdem gueltig")
        mqtt_nachricht.update("test", "nachricht")
        # jetzt Wert gesetzt --> gueltig
        self.assertTrue(mqtt_nachricht.gueltig(), msg="gueltig: Wert ok, trotzdem ungueltig")
        time.sleep(1)
        # Gueltigkeitsdauer abgelaufen --> ungueltig
        self.assertFalse(mqtt_nachricht.gueltig(), msg="gueltig: Wert abgelaufen, trotzdem gueltig")

# update implizit mit gueltig getestet
#    def update(self, pattern, nachricht):

    def test_wert(self):
        """test methode wert"""
        mqtt_nachricht = MqttNachricht("test", "testwert", 1)
        # noch kein Wert --> wert ""
        self.assertEqual(mqtt_nachricht.wert(), "", msg="wert: kein Wert gesetzt, trotzdem Wert nicht leerer String")
        mqtt_nachricht.update("test", "nachricht_wert")
        # jetzt Wert gesetzt --> wert "nachricht"
        self.assertEqual(mqtt_nachricht.wert(), "nachricht_wert", msg="wert: gesetzter Wert nicht richtig")
        mqtt_nachricht2 = MqttNachricht("test2", "testnachricht2", 10)
        MqttSup.loop_start()
        time.sleep(1) # warte, dass die Loop ans Laufen kommt
        self.assertTrue(MqttSup.publish("test2", "1234"))
        for i in range(3):
            del i
            time.sleep(1)
            ist_gueltig = mqtt_nachricht2.gueltig()
            if ist_gueltig:
                break
        self.assertTrue(ist_gueltig, msg="wert: keinen gueltigen Wert empfangen")
        self.assertEqual(mqtt_nachricht2.wert(), "1234", msg="wert: gesetzter Wert nicht richtig")
        MqttSup.loop_stop()

    def test_zeit(self):
        """test methode zeit"""
        mqtt_nachricht = MqttNachricht("testzeit", "testzeit", 1)
        # noch kein Wert --> wert ""
        self.assertEqual(mqtt_nachricht.zeit(), 0, msg="zeit: noch kein Wert gesetzt, Zeit: 0")
        jetzt = time.time()
        mqtt_nachricht.update("testzeit", "nachricht_zeit")
        # jetzt Wert gesetzt --> wert "nachricht"
        self.assertGreaterEqual(mqtt_nachricht.zeit(), jetzt, msg="zeit: gesetzter Wert nicht richtig")
        jetzt = time.time()
        self.assertLessEqual(mqtt_nachricht.zeit(), jetzt, msg="zeit: gesetzter Wert nicht richtig")


class TestShelly(unittest.TestCase):
    """Unittest fuer Shelly"""
    @classmethod
    def setUpClass(cls):
        cls._mein_shelly_gut = Shelly("shellies/shellyswitch25-745815", "gut", "192.168.2.49")

    @classmethod
    def tearDownClass(cls):
        del cls._mein_shelly_gut

    def setUp(self):
        MqttSup.loop_start()

    def tearDown(self):
        MqttSup.loop_stop()

    def test_automatic_mode_oben(self):
        """test methode Shelly:automatic_mode_oben"""
        self.assertTrue(self._mein_shelly_gut.automatic_mode(), msg="Shelly automatic_mode sollte an sein")

if __name__ == '__main__':
#    global_logger = global_logger_support()
    unittest.main()
