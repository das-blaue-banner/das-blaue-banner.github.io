# Das Blaue Banner – Website

Archivseite des Wochenblatts der Allianz auf WoW Forever. Die Seite baut sich
selbst aus dem Overleaf-Projekt: Wer dort eine Ausgabe freigibt, findet sie
spätestens eine Stunde später auf der Website.

## So funktioniert es

- Jede Stunde fragt GitHub bei Overleaf nach, ob sich etwas geändert hat.
- Wenn ja, werden alle freigegebenen Ausgaben gesetzt, in Seitenbilder
  umgewandelt und mit einer Startseite veröffentlicht.
- Freigegeben ist eine Ausgabe, wenn in `ausgaben/ausgabe-*.tex` die Zeile
  `% Freigabe: ja` steht. Ausgaben ohne diese Zeile oder mit roten
  `\platzhalter{…}` bleiben offline.
- Bereits gesetzte Ausgaben werden zwischengespeichert und nur neu gesetzt,
  wenn sich an ihnen, am Layout oder an ihren Bildern etwas ändert.

## Jede Woche

1. In Overleaf die neue Ausgabe schreiben (Kopie von `vorlagen/ausgabe-vorlage.tex`).
2. Wenn sie fertig ist, oben `% Freigabe: nein` in `% Freigabe: ja` ändern.
3. Warten oder sofort veröffentlichen: Reiter **Actions** → **Blatt
   veröffentlichen** → **Run workflow**.

## Einmalige Einrichtung

Am einfachsten mit `werkzeuge/einrichten.sh` (braucht die GitHub-Kommandozeile
`gh`): Es meldet bei GitHub an, legt das Repository an, fragt nach dem
Overleaf-Token und schaltet Pages ein. Von Hand geht es so:

1. **Secret anlegen**: Settings → Secrets and variables → Actions → New
   repository secret, Name `OVERLEAF_TOKEN`, Wert: ein Git-Token aus Overleaf
   (Account Settings → Git integration).
2. **Pages einschalten**: Settings → Pages → Source: **GitHub Actions**.

## Einstellungen

- **Suchmaschinen**: Die Seite trägt `noindex`, damit Namen und
  Discord-Zitate nicht bei Google landen. Wer gefunden werden will, legt unter
  Settings → Secrets and variables → Actions → Variables die Variable
  `SUCHMASCHINEN` mit dem Wert `ja` an.
- **Alles neu setzen**: In `werkzeuge/baue_seite.py` die Zahl bei
  `SKRIPT_STAND` erhöhen.

## Aufbau

```
.github/workflows/veroeffentlichen.yml   Ablauf auf GitHub (stündlich)
werkzeuge/baue_seite.py                  baut die Seite aus dem Overleaf-Projekt
werkzeuge/einrichten.sh                  einmalige Einrichtung auf GitHub
werkzeuge/vorlagen/rahmen.html           Seitengerüst mit Kopf und Fuß
seite/                                   Design, Schriften, Schmuckbilder
```

## Lokal bauen

Mit Podman oder Docker und einem Klon des Overleaf-Projekts in `../blaues-banner`:

```bash
podman run --rm -v "$PWD":/web -v "$PWD/../blaues-banner":/quelle:ro -w /web docker.io/texlive/texlive:latest python3 werkzeuge/baue_seite.py --quelle /quelle --ziel _site
```

Danach `_site/index.html` im Browser öffnen.

## Gut zu wissen

- Gibt es 60 Tage lang keine Änderung, schaltet GitHub den Stundenplan ab.
  Im Reiter Actions lässt er sich mit einem Klick wieder einschalten.
- Schlägt das Setzen einer Ausgabe fehl, bleibt die zuletzt gesetzte Fassung
  online und GitHub schickt eine Fehlermeldung per E-Mail.
- Schriften: IM Fell English und Cinzel unter der SIL Open Font License
  (siehe `seite/schriften/`).
