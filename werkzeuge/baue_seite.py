#!/usr/bin/env python3
"""Baut die Archivseite des Blauen Banners aus dem Overleaf-Projekt.

Jede freigegebene Ausgabe (ausgaben/ausgabe-*.tex mit der Zeile
"% Freigabe: ja") wird als PDF gesetzt, in Seitenbilder umgewandelt und
bekommt eine eigene Leseseite. Dazu kommt eine Startseite mit allen
Ausgaben.

Aufruf:
  python3 werkzeuge/baue_seite.py --quelle PFAD_ZUM_OVERLEAF_PROJEKT \\
      --ziel _site [--cache .cache] [--basis-url https://name.github.io/blaues-banner/] \\
      [--suchmaschinen ja]

Benötigt latexmk/pdflatex (TeX Live) und Ghostscript (gs).
Rückgabewert: 0 = alles gut, 3 = Seite gebaut, aber mindestens eine
Ausgabe hatte Fehler, 1 = abgebrochen.
"""

import argparse
import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Erhöhen, wenn sich das Setzen ändert und alle Ausgaben neu gebaut werden sollen
SKRIPT_STAND = "1"

WURZEL = Path(__file__).resolve().parent.parent
VORLAGEN = WURZEL / "werkzeuge" / "vorlagen"
STATISCH = WURZEL / "seite"
ZIEL_MARKE = ".blaues-banner-seite"

AUFLOESUNG_SEITE = 144     # dpi der Leseansicht, A4 ergibt etwa 1190 x 1684 Pixel
AUFLOESUNG_VORSCHAU = 60   # dpi der Vorschaubilder, A4 ergibt etwa 500 x 700 Pixel

EINLEITUNG = (
    "Kunde, Heldentaten und Gerüchte aus Sturmwind, Eisenschmiede und allen "
    "Winkeln Azeroths. Hier liegen alle Ausgaben zum Lesen und Mitnehmen bereit."
)

AKTUELLE_AUSGABE = re.compile(r"\\newcommand\{\\AktuelleAusgabe\}\{[^}]*\}")


def melde(art, text):
    """Meldung im Protokoll; auf GitHub erscheint sie an der Workflow-Ausführung."""
    if os.environ.get("GITHUB_ACTIONS"):
        text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{art}::{text}", flush=True)
    else:
        print(f"[{art}] {text}", flush=True)


# ---------------------------------------------------------------------
#  LaTeX lesen
# ---------------------------------------------------------------------
def ohne_kommentare(text):
    return re.sub(r"(?<!\\)%.*", "", text)


def lies_gruppe(text, pos, auf, zu):
    """Liest eine verschachtelte Klammergruppe ab text[pos]."""
    if pos >= len(text) or text[pos] != auf:
        return None, pos
    tiefe = 0
    i = pos
    while i < len(text):
        zeichen = text[i]
        if zeichen == "\\":
            i += 2
            continue
        if zeichen == auf:
            tiefe += 1
        elif zeichen == zu:
            tiefe -= 1
            if tiefe == 0:
                return text[pos + 1:i], i + 1
        i += 1
    return None, pos


def argumente(text, name):
    """Alle {}- und []-Gruppen direkt hinter \\name, in Reihenfolge."""
    treffer = re.search(r"\\" + re.escape(name) + r"(?![A-Za-z@])", text)
    if not treffer:
        return None
    pos = treffer.end()
    gruppen = []
    while True:
        p = pos
        while p < len(text) and text[p] in " \t" or (p < len(text) and text[p] == "\n"
                                                        and not text.startswith("\n\n", p)):
            p += 1
        if p < len(text) and text[p] in "{[":
            zu = "}" if text[p] == "{" else "]"
            inhalt, neu = lies_gruppe(text, p, text[p], zu)
            if inhalt is None:
                break
            gruppen.append((text[p], inhalt))
            pos = neu
        else:
            break
    return gruppen


def pflicht(gruppen, nummer=0):
    werte = [inhalt for art, inhalt in (gruppen or []) if art == "{"]
    return werte[nummer] if len(werte) > nummer else None


def optional(gruppen, nummer=0):
    werte = [inhalt for art, inhalt in (gruppen or []) if art == "["]
    return werte[nummer] if len(werte) > nummer else None


@dataclass
class Blatt:
    name: str = "Das Blaue Banner"
    redaktion: str = "Klio Löwenfeder"
    server: str = "WoW Forever"
    untertitel: str = "Wochenblatt für die Getreuen der Allianz"
    ort: str = "Sturmwind"

    def ersetzungen(self):
        return {"Servername": self.server, "Blattname": self.name, "Redaktion": self.redaktion}


def latex_zu_text(text, ersetzungen):
    """Macht aus einem LaTeX-Schnipsel lesbaren Text für die Website."""
    text = ohne_kommentare(text)
    for befehl, wert in ersetzungen.items():
        text = re.sub(r"\\" + befehl + r"(?![A-Za-z@])(\{\})?", lambda m: wert, text)
    text = re.sub(r"\\enquote\{([^{}]*)\}", "„\\1“", text)
    for _ in range(3):
        text = re.sub(r"\\(?:textbf|textit|emph|textsc|held|mbox|textcolor\{[^{}]*\})\{([^{}]*)\}",
                      r"\1", text)
    for alt, neu in (("\\\\", " "), ("---", "—"), ("--", "–"), ("\\,", "\u202f"),
                     ("\\ ", " "), ("\\&", "&"), ("\\%", "%"), ("\\#", "#"),
                     ("\\_", "_"), ("~", "\u00a0"), ("``", "“"), ("''", "”")):
        text = text.replace(alt, neu)
    text = re.sub(r"\\[A-Za-z@]+\*?", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def lies_blatt(quelle):
    blatt = Blatt()
    haupt = ohne_kommentare((quelle / "main.tex").read_text(encoding="utf-8"))
    felder = {"Zeitungsname": "name", "Chefredaktion": "redaktion", "Server": "server",
              "Untertitel": "untertitel", "Erscheinungsort": "ort"}
    for befehl, feld in felder.items():
        wert = pflicht(argumente(haupt, befehl))
        if wert:
            setattr(blatt, feld, latex_zu_text(wert, {}))
    return blatt


@dataclass
class Ausgabe:
    datei: Path
    nummer: int
    ordner: str
    datum: str
    titel: str
    unterzeile: str
    bilderordner: str
    seiten: list = field(default_factory=list)   # [(datei, breite, hoehe)]
    vorschau: tuple = ("vorschau.jpg", 0, 0)

    @property
    def pdf_name(self):
        return f"das-blaue-banner-nr-{self.ordner}.pdf"

    @property
    def dachzeile(self):
        return f"Nr. {self.nummer} · {self.datum}" if self.datum else f"Nr. {self.nummer}"


def finde_ausgaben(quelle, blatt):
    gefunden = []
    for datei in sorted((quelle / "ausgaben").glob("ausgabe-*.tex")):
        roh = datei.read_text(encoding="utf-8")
        name = datei.relative_to(quelle).as_posix()
        if not re.search(r"^\s*%+\s*Freigabe\s*:\s*ja\b", roh, re.IGNORECASE | re.MULTILINE):
            melde("notice", f"{name}: nicht freigegeben, wird nicht veröffentlicht.")
            continue
        text = ohne_kommentare(roh)
        if "\\platzhalter{" in text:
            melde("warning", f"{name}: enthält noch \\platzhalter, wird nicht veröffentlicht.")
            continue
        nummer_text = latex_zu_text(pflicht(argumente(text, "Ausgabe")) or "", {})
        if nummer_text.isdigit():
            nummer = int(nummer_text)
        else:
            ziffern = re.findall(r"\d+", datei.stem)
            if not ziffern:
                melde("warning", f"{name}: keine Ausgabennummer gefunden, übersprungen.")
                continue
            nummer = int(ziffern[-1])
        aufmacher = argumente(text, "aufmacher")
        gefunden.append(Ausgabe(
            datei=datei,
            nummer=nummer,
            ordner=f"{nummer:03d}",
            datum=latex_zu_text(pflicht(argumente(text, "Datum")) or "", blatt.ersetzungen()),
            titel=latex_zu_text(pflicht(aufmacher) or f"Ausgabe Nr. {nummer}", blatt.ersetzungen()),
            unterzeile=latex_zu_text(optional(aufmacher) or "", blatt.ersetzungen()),
            bilderordner=latex_zu_text(pflicht(argumente(text, "Bilderordner")) or "", {}),
        ))
    nummern = [a.nummer for a in gefunden]
    doppelt = {n for n in nummern if nummern.count(n) > 1}
    if doppelt:
        melde("error", f"Ausgabennummer mehrfach vergeben: {sorted(doppelt)}. "
                       "Nur die jeweils letzte Datei wird veröffentlicht.")
        eindeutig = {}
        for ausgabe in gefunden:
            eindeutig[ausgabe.nummer] = ausgabe
        gefunden = list(eindeutig.values())
    return sorted(gefunden, key=lambda a: a.nummer, reverse=True)


# ---------------------------------------------------------------------
#  Setzen und Bilder
# ---------------------------------------------------------------------
def eingabe_stand(quelle, ausgabe):
    """Prüfsumme über alles, was das Aussehen einer Ausgabe beeinflusst."""
    summe = hashlib.sha256(SKRIPT_STAND.encode())
    haupt = AKTUELLE_AUSGABE.sub("", (quelle / "main.tex").read_text(encoding="utf-8"))
    summe.update(haupt.encode("utf-8"))
    dateien = sorted(quelle.glob("*.sty")) + [ausgabe.datei]
    bilder = quelle / "bilder"
    if bilder.is_dir():
        dateien += sorted(f for f in bilder.glob("*") if f.is_file())
        unterordner = ["layout", "archiv"] + ([ausgabe.bilderordner] if ausgabe.bilderordner else [])
        for ordner in unterordner:
            if (bilder / ordner).is_dir():
                dateien += sorted(f for f in (bilder / ordner).rglob("*") if f.is_file())
    for datei in dateien:
        summe.update(datei.relative_to(quelle).as_posix().encode("utf-8"))
        summe.update(datei.read_bytes())
    return summe.hexdigest()


def setze(quelle, ausgabe, arbeit):
    """Setzt eine Ausgabe mit latexmk. Gibt (pdf oder None, rueckgabewert, fehlerzeilen)."""
    projekt = arbeit / "projekt"
    if not projekt.exists():
        shutil.copytree(quelle, projekt, ignore=shutil.ignore_patterns(".git"))
    haupt = (projekt / "main.tex").read_text(encoding="utf-8")
    pfad = ausgabe.datei.relative_to(quelle).with_suffix("").as_posix()
    neu, anzahl = AKTUELLE_AUSGABE.subn(
        lambda m: "\\newcommand{\\AktuelleAusgabe}{" + pfad + "}", haupt)
    if anzahl != 1:
        raise RuntimeError("In main.tex fehlt die Zeile \\newcommand{\\AktuelleAusgabe}{...}.")
    jobname = Path(ausgabe.pdf_name).stem
    tex = projekt / f"_{jobname}.tex"
    tex.write_text(neu, encoding="utf-8")
    aus = arbeit / "aus" / ausgabe.ordner
    aus.mkdir(parents=True, exist_ok=True)
    ergebnis = subprocess.run(
        ["latexmk", "-pdf", "-interaction=nonstopmode", "-file-line-error",
         f"-outdir={aus}", f"-jobname={jobname}", tex.name],
        cwd=projekt, capture_output=True, text=True, errors="replace", timeout=900)
    fehler = []
    protokoll = aus / f"{jobname}.log"
    if protokoll.exists():
        for zeile in protokoll.read_text(encoding="latin-1").splitlines():
            if zeile.startswith("!") or re.match(r"^\S+\.tex:\d+: ", zeile):
                fehler.append(zeile.strip())
    pdf = aus / f"{jobname}.pdf"
    return (pdf if pdf.exists() else None), ergebnis.returncode, fehler


def ghostscript(argumente_gs):
    subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE",
                    "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4"] + argumente_gs,
                   check=True, capture_output=True)


def bilder_erzeugen(pdf, ablage):
    for alt in ablage.glob("seite-*.jpg"):
        alt.unlink()
    ghostscript(["-sDEVICE=jpeg", "-dJPEGQ=82", f"-r{AUFLOESUNG_SEITE}",
                 f"-sOutputFile={ablage}/seite-%d.jpg", str(pdf)])
    ghostscript(["-sDEVICE=jpeg", "-dJPEGQ=80", f"-r{AUFLOESUNG_VORSCHAU}",
                 "-dFirstPage=1", "-dLastPage=1",
                 f"-sOutputFile={ablage}/vorschau.jpg", str(pdf)])


def jpeg_groesse(pfad):
    daten = pfad.read_bytes()
    i = 2
    while i + 9 < len(daten):
        if daten[i] != 0xFF:
            i += 1
            continue
        marke = daten[i + 1]
        if marke in (0xC0, 0xC1, 0xC2):
            hoehe = int.from_bytes(daten[i + 5:i + 7], "big")
            breite = int.from_bytes(daten[i + 7:i + 9], "big")
            return breite, hoehe
        if marke == 0xFF or 0xD0 <= marke <= 0xD9 or marke == 0x01:
            i += 2
            continue
        i += 2 + int.from_bytes(daten[i + 2:i + 4], "big")
    return 0, 0


def bereite_vor(quelle, ausgabe, cache, arbeit):
    """Setzt die Ausgabe neu oder nimmt sie aus dem Zwischenspeicher.

    Gibt (ablageordner oder None, fehler_aufgetreten) zurück."""
    ablage = cache / f"ausgabe-{ausgabe.ordner}"
    stand = eingabe_stand(quelle, ausgabe)
    standdatei = ablage / "stand.txt"
    vollstaendig = (ablage / ausgabe.pdf_name).is_file() and (ablage / "vorschau.jpg").is_file()
    if vollstaendig and standdatei.is_file() and standdatei.read_text().strip() == stand:
        print(f"Nr. {ausgabe.nummer}: unverändert, aus dem Zwischenspeicher.", flush=True)
        return ablage, False

    print(f"Nr. {ausgabe.nummer}: wird gesetzt ...", flush=True)
    pdf, rueckgabe, fehler = setze(quelle, ausgabe, arbeit)
    auszug = " | ".join(fehler[:3])
    if pdf is None:
        melde("error", f"Nr. {ausgabe.nummer} ({ausgabe.datei.name}): Setzen fehlgeschlagen. {auszug}")
        if vollstaendig:
            melde("warning", f"Nr. {ausgabe.nummer}: Die zuletzt gesetzte Fassung bleibt online.")
            return ablage, True
        return None, True
    hatte_fehler = rueckgabe != 0
    if hatte_fehler:
        melde("error", f"Nr. {ausgabe.nummer} ({ausgabe.datei.name}): LaTeX meldet Fehler, "
                       f"das PDF wurde trotzdem veröffentlicht. {auszug}")
    ablage.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, ablage / ausgabe.pdf_name)
    bilder_erzeugen(ablage / ausgabe.pdf_name, ablage)
    standdatei.write_text(stand)
    return ablage, hatte_fehler


# ---------------------------------------------------------------------
#  HTML
# ---------------------------------------------------------------------
def esc(text):
    return html.escape(text, quote=True)


def rahmen(werte):
    seite = (VORLAGEN / "rahmen.html").read_text(encoding="utf-8")
    for schluessel, wert in werte.items():
        seite = seite.replace("{{" + schluessel + "}}", wert)
    uebrig = re.findall(r"\{\{[a-z_]+\}\}", seite)
    if uebrig:
        raise RuntimeError(f"Unbekannte Platzhalter in rahmen.html: {uebrig}")
    return seite


def og_angaben(blatt, basis_url, pfad, titel, beschreibung, bild, art):
    if not basis_url:
        return ""
    zeilen = [
        f'<meta property="og:type" content="{art}">',
        f'<meta property="og:site_name" content="{esc(blatt.name)}">',
        '<meta property="og:locale" content="de_DE">',
        f'<meta property="og:title" content="{esc(titel)}">',
        f'<meta property="og:description" content="{esc(beschreibung)}">',
        f'<meta property="og:url" content="{esc(basis_url + pfad)}">',
    ]
    if bild:
        bildpfad, breite, hoehe = bild
        zeilen += [
            f'<meta property="og:image" content="{esc(basis_url + bildpfad)}">',
            f'<meta property="og:image:width" content="{breite}">',
            f'<meta property="og:image:height" content="{hoehe}">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]
    return "\n".join(zeilen)


def gemeinsame_werte(blatt, basis, robots):
    return {
        "basis": basis,
        "robots": robots,
        "blattname": esc(blatt.name),
        "untertitel": esc(blatt.untertitel),
        "ort": esc(blatt.ort),
        "server": esc(blatt.server),
        "redaktion": esc(blatt.redaktion),
    }


def startseite(blatt, ausgaben, basis_url, robots):
    teile = [f'    <p class="einleitung">{esc(EINLEITUNG)}</p>']
    if ausgaben:
        neu = ausgaben[0]
        bild, breite, hoehe = neu.vorschau
        link = f"ausgaben/{neu.ordner}/"
        unterzeile = f'\n        <p class="unterzeile">{esc(neu.unterzeile)}</p>' if neu.unterzeile else ""
        teile.append(f"""
    <h2 class="band"><span>Die neueste Ausgabe</span></h2>
    <article class="neueste">
      <a class="neueste-bild" href="{link}"><img class="papier" src="{link}{bild}" alt="Titelseite der Ausgabe Nr. {neu.nummer}" width="{breite}" height="{hoehe}"></a>
      <div class="neueste-text">
        <p class="dachzeile">{esc(neu.dachzeile)}</p>
        <h3 class="schlagzeile"><a href="{link}">{esc(neu.titel)}</a></h3>{unterzeile}
        <div class="knoepfe">
          <a class="knopf" href="{link}">Ausgabe lesen</a>
          <a class="knopf knopf-zweit" href="{link}{neu.pdf_name}">Als PDF</a>
        </div>
      </div>
    </article>""")
        if len(ausgaben) > 1:
            karten = []
            for alt in ausgaben[1:]:
                bild, breite, hoehe = alt.vorschau
                link = f"ausgaben/{alt.ordner}/"
                karten.append(
                    f'      <li><a class="karte" href="{link}">'
                    f'<img class="papier" src="{link}{bild}" alt="" width="{breite}" height="{hoehe}" loading="lazy">'
                    f'<span class="karte-nr">{esc(alt.dachzeile)}</span>'
                    f'<span class="karte-titel">{esc(alt.titel)}</span></a></li>')
            teile.append('\n    <h2 class="band"><span>Frühere Ausgaben</span></h2>\n'
                         '    <ul class="archiv">\n' + "\n".join(karten) + "\n    </ul>")
    else:
        teile.append('\n    <h2 class="band"><span>Die Ausgaben</span></h2>\n'
                     '    <p class="leer">Noch ist keine Ausgabe erschienen. '
                     'Die Feder ist gespitzt, schaut bald wieder vorbei.</p>')

    beschreibung = f"{blatt.untertitel} auf {blatt.server}. Alle Ausgaben zum Lesen und Herunterladen."
    werte = gemeinsame_werte(blatt, "", robots)
    werte.update({
        "seitentitel": esc(blatt.name),
        "beschreibung": esc(beschreibung),
        "og": og_angaben(blatt, basis_url, "", blatt.name, beschreibung,
                         (f"ausgaben/{ausgaben[0].ordner}/{ausgaben[0].vorschau[0]}",
                          ausgaben[0].vorschau[1], ausgaben[0].vorschau[2]) if ausgaben else None,
                         "website"),
        "titel_tag": "h1",
        "leiste_mitte": "Archiv aller Ausgaben",
        "inhalt": "\n".join(teile),
    })
    return rahmen(werte)


def ausgabeseite(blatt, ausgabe, neuere, aeltere, basis_url, robots):
    seiten = []
    for nummer, (datei, breite, hoehe) in enumerate(ausgabe.seiten, start=1):
        laden = "eager" if nummer == 1 else "lazy"
        seiten.append(
            f'      <figure class="seite"><a href="{datei}"><img class="papier" src="{datei}" '
            f'alt="Seite {nummer} der Ausgabe Nr. {ausgabe.nummer}" width="{breite}" height="{hoehe}" '
            f'loading="{laden}"></a><figcaption>Seite {nummer}</figcaption></figure>')
    blaettern = []
    if aeltere:
        blaettern.append(f'<a href="../{aeltere.ordner}/"><span class="blaettern-art">Ältere Ausgabe</span>'
                         f'<span class="blaettern-titel">Nr. {aeltere.nummer}: {esc(aeltere.titel)}</span></a>')
    if neuere:
        blaettern.append(f'<a class="weiter" href="../{neuere.ordner}/"><span class="blaettern-art">Neuere Ausgabe</span>'
                         f'<span class="blaettern-titel">Nr. {neuere.nummer}: {esc(neuere.titel)}</span></a>')
    navigation = (f'\n    <nav class="blaettern" aria-label="Weitere Ausgaben">{"".join(blaettern)}</nav>'
                  if blaettern else "")
    unterzeile = f'\n      <p class="unterzeile">{esc(ausgabe.unterzeile)}</p>' if ausgabe.unterzeile else ""
    inhalt = f"""    <p class="zurueck"><a href="../../">Zurück zum Archiv</a></p>
    <article>
      <header class="ausgabe-kopf">
        <p class="dachzeile">{esc(ausgabe.dachzeile)}</p>
        <h1 class="schlagzeile">{esc(ausgabe.titel)}</h1>{unterzeile}
        <div class="knoepfe"><a class="knopf" href="{ausgabe.pdf_name}">Als PDF herunterladen</a></div>
      </header>
      <div class="seiten">
{chr(10).join(seiten)}
      </div>
    </article>{navigation}"""
    titel = f"{blatt.name}, Nr. {ausgabe.nummer}: {ausgabe.titel}"
    beschreibung = ausgabe.unterzeile or f"{blatt.untertitel} auf {blatt.server}."
    werte = gemeinsame_werte(blatt, "../../", robots)
    werte.update({
        "seitentitel": esc(f"Nr. {ausgabe.nummer}: {ausgabe.titel} · {blatt.name}"),
        "beschreibung": esc(beschreibung),
        "og": og_angaben(blatt, basis_url, f"ausgaben/{ausgabe.ordner}/", titel, beschreibung,
                         (f"ausgaben/{ausgabe.ordner}/{ausgabe.vorschau[0]}",
                          ausgabe.vorschau[1], ausgabe.vorschau[2]), "article"),
        "titel_tag": "p",
        "leiste_mitte": esc(ausgabe.dachzeile),
        "inhalt": inhalt,
    })
    return rahmen(werte)


# ---------------------------------------------------------------------
#  Ablauf
# ---------------------------------------------------------------------
def leere_ziel(ziel):
    if ziel.exists():
        if any(ziel.iterdir()) and not (ziel / ZIEL_MARKE).exists():
            raise RuntimeError(f"{ziel} ist nicht leer und stammt nicht von diesem Skript. "
                               "Bitte einen leeren oder neuen Ordner als --ziel angeben.")
        shutil.rmtree(ziel)


def main():
    parser = argparse.ArgumentParser(description="Baut die Archivseite des Blauen Banners.")
    parser.add_argument("--quelle", required=True, help="Ordner mit dem Overleaf-Projekt")
    parser.add_argument("--ziel", default="_site", help="Ausgabeordner der Website")
    parser.add_argument("--cache", default=".cache", help="Zwischenspeicher für gesetzte Ausgaben")
    parser.add_argument("--basis-url", default="", help="Öffentliche Adresse der Seite (für Vorschauen)")
    parser.add_argument("--suchmaschinen", default="", help="'ja' erlaubt Suchmaschinen das Aufnehmen")
    args = parser.parse_args()

    quelle = Path(args.quelle).resolve()
    ziel = Path(args.ziel).resolve()
    cache = Path(args.cache).resolve()
    basis_url = args.basis_url.strip()
    if basis_url and not basis_url.endswith("/"):
        basis_url += "/"
    robots = ("" if args.suchmaschinen.strip().lower() == "ja"
              else '<meta name="robots" content="noindex">')

    if not (quelle / "main.tex").is_file():
        melde("error", f"Keine main.tex in {quelle} gefunden.")
        return 1

    blatt = lies_blatt(quelle)
    ausgaben = finde_ausgaben(quelle, blatt)
    leere_ziel(ziel)
    shutil.copytree(STATISCH, ziel)
    (ziel / ZIEL_MARKE).write_text("Von werkzeuge/baue_seite.py erzeugt.\n")
    (ziel / ".nojekyll").write_text("")
    cache.mkdir(parents=True, exist_ok=True)

    fehler = False
    veroeffentlicht = []
    with tempfile.TemporaryDirectory(prefix="blaues-banner-") as arbeitsordner:
        arbeit = Path(arbeitsordner)
        for ausgabe in ausgaben:
            try:
                ablage, hatte_fehler = bereite_vor(quelle, ausgabe, cache, arbeit)
            except (subprocess.SubprocessError, OSError, RuntimeError) as problem:
                melde("error", f"Nr. {ausgabe.nummer}: {problem}")
                fehler = True
                continue
            fehler = fehler or hatte_fehler
            if ablage is None:
                continue
            ordner = ziel / "ausgaben" / ausgabe.ordner
            ordner.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ablage / ausgabe.pdf_name, ordner / ausgabe.pdf_name)
            shutil.copy2(ablage / "vorschau.jpg", ordner / "vorschau.jpg")
            ausgabe.vorschau = ("vorschau.jpg", *jpeg_groesse(ordner / "vorschau.jpg"))
            seitenbilder = sorted(ablage.glob("seite-*.jpg"),
                                  key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
            for bild in seitenbilder:
                shutil.copy2(bild, ordner / bild.name)
                ausgabe.seiten.append((bild.name, *jpeg_groesse(bild)))
            veroeffentlicht.append(ausgabe)

    for index, ausgabe in enumerate(veroeffentlicht):
        neuere = veroeffentlicht[index - 1] if index > 0 else None
        aeltere = veroeffentlicht[index + 1] if index + 1 < len(veroeffentlicht) else None
        seite = ausgabeseite(blatt, ausgabe, neuere, aeltere, basis_url, robots)
        (ziel / "ausgaben" / ausgabe.ordner / "index.html").write_text(seite, encoding="utf-8")
    (ziel / "index.html").write_text(startseite(blatt, veroeffentlicht, basis_url, robots),
                                     encoding="utf-8")

    liste = ", ".join(f"Nr. {a.nummer}" for a in veroeffentlicht) or "keine"
    print(f"Fertig. Veröffentlicht: {liste}.", flush=True)
    return 3 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
