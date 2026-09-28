#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logique partagée pour dialoguer avec le BMS BQ30z55 via comm_sbs_bqctrl.py,
sans dépendre de tkinter. Utilisée par gui_app.py (Tkinter) et web_app.py (Flask).
"""

import os
import types
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
CHIP_DRIVER_FILE = os.path.join(HERE, "comm_sbs_chips", "BQ30z554.py")
MAIN_MODULE_FILE = os.path.join(HERE, "comm_sbs_bqctrl.py")


def load_bq_module():
    """ Charge comm_sbs_bqctrl.py comme bibliothèque (pas d'exécution de main()). """
    spec = importlib.util.spec_from_file_location("comm_sbs_bqctrl", MAIN_MODULE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bq = load_bq_module()


def load_chip_driver(po):
    """ Reproduit ce que fait main() dans comm_sbs_bqctrl.py :
    exécute le fichier de la puce dans l'espace de noms global du module,
    avec `po` accessible pendant l'exécution (le fichier de puce lit po.chip).
    """
    bq.reset_default_driver(po)
    with open(CHIP_DRIVER_FILE, "rb") as f:
        code = compile(f.read(), CHIP_DRIVER_FILE, "exec")
    exec(code, vars(bq), {"po": po})


def make_po(bus_str, dev_address, sha1key, verbose=1, dry_run=False):
    po = types.SimpleNamespace()
    po.bus = bus_str
    po.dev_address = dev_address
    po.chip = bq.CHIP_TYPE.BQ30z55
    po.dry_run = dry_run
    po.verbose = verbose
    po.explain = False
    po.short = False
    po.sha1key = sha1key
    po.i32key = None
    po.api_type = None
    po.offline_mode = False
    return po


READ_SHORTCUTS = [
    "BatteryStatus", "Voltage", "Current", "Temperature",
    "CycleCount", "RelativeStateOfCharge", "DesignCapacity",
]

MONITOR_GROUPS = [m.name for m in bq.MONITOR_GROUP]

SEAL_ACTIONS = ["Unseal", "FullAccess", "Seal"]

# Commande confirmée (dans comm_sbs_chips/BQ30z554.py) pour effacer le flag
# Permanent Failure sur BQ30z55 / BQ30z554.
CLEAR_PF_COMMAND = "ManufacturerAccess.PermanentFailDataReset"

# Équivalent logiciel du pont RST->GND : redémarre le microcontrôleur du
# BMS (Full Device Reset + AFE Reset), sans effacer aucun flag.
REBOOT_COMMAND = "ManufacturerAccess.DeviceReset"


class Session:
    """ Encapsule une connexion active (po + vals) pour un client GUI ou web. """
    def __init__(self):
        self.po = None
        self.vals = {}
        self.connected = False

    def connect(self, bus_str, dev_address, sha1key):
        po = make_po(bus_str, dev_address, sha1key)
        bq.smbus_open(bus_str, po)
        load_chip_driver(po)
        self.po = po
        self.vals = {}
        self.connected = True
        return po

    def require_connected(self):
        if not self.connected or self.po is None:
            raise RuntimeError("Non connecté — cliquez d'abord sur Connecter.")

    def read(self, cmd_str):
        self.require_connected()
        bq.smart_battery_system_read(cmd_str, self.vals, self.po)

    def identify(self, bus_str, dev_address, dry_run=False):
        """ Détecte électroniquement le modèle exact de puce (fonctionne même
        batterie scellée) — lit le FirmwareVersion via la séquence spéciale
        utilisée par comm_sbs_bqctrl.py en mode --chip AUTO.

        IMPORTANT : reset_default_driver() réinitialise des variables
        globales du module (SBS_CMD_GROUPS, SBS_CMD_INFO, SBS_SEALING...)
        partagées par toute la session. Sans recharger ensuite le profil
        complet de la puce, les groupes spécifiques BQ (BQCellVoltages,
        BQStatusBits...) disparaissent silencieusement pour tout le monde,
        même après un Connect réussi auparavant. On recharge donc
        systématiquement le profil BQ30z554 à la fin, quel que soit le
        résultat de la détection, pour ne jamais laisser le module dans
        un état partiellement réinitialisé.
        """
        po = make_po(bus_str, dev_address, sha1key="", dry_run=dry_run)
        po.chip = bq.CHIP_TYPE.AUTO
        bq.smbus_open(bus_str, po)
        bq.reset_default_driver(po)
        detected = bq.smart_battery_detect({}, po)
        doc = detected.__doc__ or ""
        print("Puce détectée : {} — {}".format(detected.name, doc))
        if detected.name in ("BQ30z55", "BQ30z554", "BQ30z50"):
            print("Correspond au profil chargé (comm_sbs_chips/BQ30z554.py). OK.")
        elif detected.name == "BQGENERIC":
            print("Réponse reçue mais numéro de puce non reconnu dans la liste "
                  "connue — vérifiez le câblage/adresse si ce résultat surprend.")
        else:
            print("Attention : ceci diffère du profil actuellement chargé "
                  "(BQ30z554) — les commandes spécifiques (Unseal, PermanentFailDataReset...) "
                  "pourraient ne pas correspondre exactement à cette puce.")

        # Toujours recharger le profil complet BQ30z554 avant de rendre la
        # main, pour ne jamais laisser le module dans l'état "reset partiel"
        # (SBS_CMD_GROUPS réduit aux 5 groupes de base uniquement).
        if self.connected and self.po is not None:
            load_chip_driver(self.po)
            print("Profil de puce de la session active rechargé (BQCellVoltages, "
                  "BQStatusBits, etc. de nouveau disponibles).")
        else:
            po.chip = bq.CHIP_TYPE.BQ30z55
            load_chip_driver(po)

        return detected

    def monitor(self, group_str):
        self.require_connected()
        bq.smart_battery_system_monitor(group_str, self.vals, self.po)

    def sealing(self, action):
        self.require_connected()
        bq.smart_battery_system_sealing(action, self.vals, self.po)

    def trigger(self, cmd_str):
        self.require_connected()
        bq.smart_battery_system_trigger(cmd_str, self.vals, self.po)
