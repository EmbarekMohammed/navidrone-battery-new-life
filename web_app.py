#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interface web (Flask) pour débloquer/diagnostiquer une batterie DJI
(BQ30z55) via I2C/SMBus — sans avoir besoin de X11 ni de VNC.

Le serveur tourne sur le Pi ; on y accède depuis n'importe quel navigateur
(PC, téléphone) sur le même réseau.

Installation :
    pip3 install flask smbus2

Lancement sur le Pi :
    python3 web_app.py
    -> ouvrez http://<ip-du-pi>:5000 depuis un navigateur

Ce serveur n'est pas conçu pour être exposé sur Internet : il n'y a
aucune authentification. Gardez-le sur votre réseau local.
"""

import os
import sys
import io
import threading
from flask import Flask, request, jsonify, render_template_string

import bq_bridge as bridge

app = Flask(__name__)
session = bridge.Session()

HERE = os.path.dirname(os.path.abspath(__file__))
APP_TITLE = "NaviDrone Battery New Life"

console_lines = []
console_lock = threading.Lock()
busy_lock = threading.Lock()
busy = False


class ConsoleCapture(io.TextIOBase):
    def write(self, s):
        if s:
            with console_lock:
                console_lines.append(s)
                # garde un historique raisonnable
                if len(console_lines) > 2000:
                    del console_lines[:1000]
        return len(s)

    def flush(self):
        pass


def run_task(fn, *args):
    global busy
    with busy_lock:
        if busy:
            return False, "Une opération est déjà en cours."
        busy = True

    def worker():
        global busy
        old_stdout = sys.stdout
        sys.stdout = ConsoleCapture()
        try:
            fn(*args)
        except Exception as ex:
            with console_lock:
                console_lines.append("Erreur: {}: {}\n".format(type(ex).__name__, ex))
        finally:
            sys.stdout = old_stdout
            with busy_lock:
                busy = False

    threading.Thread(target=worker, daemon=True).start()
    return True, "Lancé."


PAGE = """
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{{ app_title }}</title>
<style>
  body { font-family: system-ui, sans-serif; background: #f4f4f4; margin: 0; padding: 16px; }
  .app-header { margin-bottom: 6px; }
  .app-header h1 { font-size: 1.3rem; margin: 0; }
  .app-header .subtitle { font-size: 0.8rem; color: #666; margin: 2px 0 0 0; }
  fieldset { margin-bottom: 14px; border-radius: 8px; border: 1px solid #ccc; background: white; }
  legend { font-weight: 600; padding: 0 6px; }
  label { display: inline-block; min-width: 110px; }
  input, select { padding: 4px 6px; margin: 4px 0; }
  button { padding: 6px 14px; margin: 4px 4px 4px 0; cursor: pointer; border-radius: 6px;
           border: 1px solid #888; background: #eee; }
  button:hover { background: #ddd; }
  button.danger { background: #ffdddd; border-color: #c00; }
  button.danger:hover { background: #ffbbbb; }
  #status { font-weight: 600; }
  #status.connected { color: green; }
  #status.disconnected { color: #c00; }
  #console { background: #000; color: #0f6; font-family: 'Courier New', monospace;
             font-size: 0.85rem; padding: 10px; height: 320px; overflow-y: auto;
             white-space: pre-wrap; border-radius: 8px;
             user-select: text; -webkit-user-select: text; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
</style>
</head>
<body>
<div class="app-header">
  <h1>{{ app_title }}</h1>
  <p class="subtitle">DJI Mavic Pro (BQ30z55) — I2C/SMBus</p>
</div>


<fieldset>
  <legend>1. Connexion</legend>
  <div class="row">
    <label>Bus</label><input id="bus" value="i2c:1" size="8">
    <label>Adresse</label><input id="addr" value="0x0b" size="6">
  </div>
  <div class="row">
    <label>Clé SHA-1</label><input id="key" value="0123456789abcdeffedcba9876543210" size="40">
  </div>
  <button onclick="connect()">Connecter</button>
  <button onclick="identify()">Identifier la puce</button>
  <span id="status" class="disconnected">Non connecté</span>
  <p style="margin:4px 0 0 0; color:#555; font-size:0.85rem;">
    « Identifier la puce » lit le numéro de puce directement en I2C (fonctionne
    même batterie scellée, sans rien modifier) — utile pour confirmer le
    modèle exact avant de se fier au marquage imprimé, souvent masqué par
    le vernis de protection.
  </p>
</fieldset>

<fieldset>
  <legend>2. Lecture</legend>
  <div class="row">
    <label>Commande</label>
    <select id="readcmd">
      {% for c in read_shortcuts %}<option>{{c}}</option>{% endfor %}
    </select>
    <button onclick="doRead()">Lire</button>
  </div>
  <div class="row">
    <label>Groupe monitor</label>
    <select id="monitorgrp">
      {% for g in monitor_groups %}<option {% if g=='BQCellVoltages' %}selected{% endif %}>{{g}}</option>{% endfor %}
    </select>
    <button onclick="doMonitor()">Monitor</button>
  </div>
</fieldset>

<fieldset>
  <legend>3. Sealing</legend>
  {% for a in seal_actions %}
  <button onclick="doSealing('{{a}}')">{{a}}</button>
  {% endfor %}
</fieldset>

<fieldset>
  <legend>4. Effacer le flag Permanent Failure (écriture)</legend>
  <div class="row">
    <label>Trigger</label>
    <input id="triggercmd" value="{{ clear_pf_command }}" size="45">
    <button class="danger" onclick="doTrigger()">Exécuter</button>
  </div>
</fieldset>

<fieldset>
  <legend>5. Redémarrer le contrôleur (reset logiciel)</legend>
  <p style="margin:4px 0; color:#555; font-size:0.9rem;">
    Équivalent logiciel du pont RST→GND fait à la main sur la carte.
    Deux usages : (1) en dépannage, avant Unseal, si le bouton de la
    batterie ne répond plus ; (2) en validation finale, <strong>après
    Seal</strong> — un reset matériel reverrouille automatiquement la
    puce (équivaut à un Seal), donc l'utiliser en fin de procédure permet
    de vérifier que le flag PF ne revient pas après un reset complet.
  </p>
  <button onclick="doReboot()">Redémarrer (DeviceReset)</button>
</fieldset>

<fieldset>
  <legend>Console</legend>
  <div id="console"></div>
</fieldset>

<script>
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify(body || {})});
  return r.json();
}

function connect() {
  post('/api/connect', {bus: document.getElementById('bus').value,
                         addr: document.getElementById('addr').value,
                         key: document.getElementById('key').value})
    .then(d => { if (!d.ok) alert(d.error || 'Erreur'); });
}

function identify() {
  post('/api/identify', {bus: document.getElementById('bus').value,
                          addr: document.getElementById('addr').value})
    .then(d => { if (!d.ok) alert(d.error || 'Erreur'); });
}

function doRead() {
  post('/api/read', {cmd: document.getElementById('readcmd').value})
    .then(d => { if (!d.ok) alert(d.error); });
}
function doMonitor() {
  post('/api/monitor', {group: document.getElementById('monitorgrp').value})
    .then(d => { if (!d.ok) alert(d.error); });
}
function doSealing(action) {
  if (action !== 'Seal' && !confirm("Confirmez l'opération '" + action + "' ?")) return;
  post('/api/sealing', {action: action}).then(d => { if (!d.ok) alert(d.error); });
}
function doReboot() {
  if (!confirm("Redémarrer le contrôleur du BMS (DeviceReset) ?")) return;
  post('/api/trigger', {cmd: '{{ reboot_command }}'}).then(d => { if (!d.ok) alert(d.error); });
}
function doTrigger() {
  const cmd = document.getElementById('triggercmd').value;
  if (!confirm("Ceci va exécuter le trigger :\\n\\n" + cmd +
               "\\n\\nCette action modifie l'état de la batterie. Continuer ?")) return;
  post('/api/trigger', {cmd: cmd}).then(d => { if (!d.ok) alert(d.error); });
}

async function pollStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  const el = document.getElementById('status');
  el.textContent = d.connected ? ('Connecté (' + d.bus + ', addr=' + d.addr + ')') : 'Non connecté';
  el.className = d.connected ? 'connected' : 'disconnected';
}

let lastConsoleText = "";

function consoleHasSelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return false;
  const el = document.getElementById('console');
  return el.contains(sel.anchorNode);
}

async function pollConsole() {
  const r = await fetch('/api/console');
  const d = await r.json();
  if (d.text === lastConsoleText) return; // rien de nouveau : ne pas toucher au DOM

  const el = document.getElementById('console');
  if (consoleHasSelection()) return; // laisser l'utilisateur copier tranquillement

  // "Sticky scroll" : ne recolle en bas que si l'utilisateur y était déjà
  // (tolérance de 20px pour les petits écarts d'arrondi du navigateur)
  const wasNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;

  lastConsoleText = d.text;
  el.textContent = d.text;

  if (wasNearBottom) {
    el.scrollTop = el.scrollHeight;
  }
}

setInterval(pollConsole, 700);
setInterval(pollStatus, 1500);
pollConsole(); pollStatus();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        PAGE,
        read_shortcuts=bridge.READ_SHORTCUTS,
        monitor_groups=bridge.MONITOR_GROUPS,
        seal_actions=bridge.SEAL_ACTIONS,
        clear_pf_command=bridge.CLEAR_PF_COMMAND,
        reboot_command=bridge.REBOOT_COMMAND,
        app_title=APP_TITLE,
    )


@app.route("/api/status")
def api_status():
    addr = "{:#x}".format(session.po.dev_address) if session.po else ""
    bus_str = session.po.bus if session.po else ""
    return jsonify(connected=session.connected, bus=bus_str, addr=addr)


@app.route("/api/console")
def api_console():
    with console_lock:
        text = "".join(console_lines)
    return jsonify(text=text)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.get_json(force=True)
    bus_str = data.get("bus", "i2c:1").strip()
    try:
        addr = int(data.get("addr", "0x0b").strip(), 0)
    except ValueError:
        return jsonify(ok=False, error="Adresse invalide"), 400
    key = data.get("key", "").strip()

    def task():
        print("Ouverture du bus {} ...".format(bus_str))
        session.connect(bus_str, addr, key)
        print("Connecté.")

    ok, msg = run_task(task)
    return jsonify(ok=ok, error=None if ok else msg)


@app.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(force=True)
    bus_str = data.get("bus", "i2c:1").strip()
    try:
        addr = int(data.get("addr", "0x0b").strip(), 0)
    except ValueError:
        return jsonify(ok=False, error="Adresse invalide"), 400

    def task():
        print("Identification de la puce sur {} ...".format(bus_str))
        session.identify(bus_str, addr)

    ok, msg = run_task(task)
    return jsonify(ok=ok, error=None if ok else msg)


@app.route("/api/read", methods=["POST"])
def api_read():
    data = request.get_json(force=True)
    cmd = data.get("cmd", "").strip()
    ok, msg = run_task(session.read, cmd)
    return jsonify(ok=ok, error=None if ok else msg)


@app.route("/api/monitor", methods=["POST"])
def api_monitor():
    data = request.get_json(force=True)
    group = data.get("group", "").strip()
    ok, msg = run_task(session.monitor, group)
    return jsonify(ok=ok, error=None if ok else msg)


@app.route("/api/sealing", methods=["POST"])
def api_sealing():
    data = request.get_json(force=True)
    action = data.get("action", "").strip()
    ok, msg = run_task(session.sealing, action)
    return jsonify(ok=ok, error=None if ok else msg)


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    data = request.get_json(force=True)
    cmd = data.get("cmd", "").strip()
    ok, msg = run_task(session.trigger, cmd)
    return jsonify(ok=ok, error=None if ok else msg)


if __name__ == "__main__":
    print("Serveur sur http://0.0.0.0:5000 — accédez-y via http://<ip-du-pi>:5000")
    app.run(host="0.0.0.0", port=5000, threaded=True)
