#!/usr/bin/env bash
# Richtet die Website einmalig auf GitHub ein:
# Anmeldung, Repository anlegen, Overleaf-Token als Secret hinterlegen,
# GitHub Pages einschalten und den ersten Bau anstoßen.
set -euo pipefail

REPO_NAME="blaues-banner"
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "Die GitHub-Kommandozeile fehlt. Bitte zuerst ausführen:  sudo dnf install gh"
  exit 1
fi

echo
echo "== 1/5  Bei GitHub anmelden"
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  gh auth login --hostname github.com --git-protocol https --web --scopes workflow
elif ! gh auth status --hostname github.com 2>&1 | grep -q "workflow"; then
  gh auth refresh --hostname github.com --scopes workflow
fi
gh auth setup-git --hostname github.com
BENUTZER=$(gh api user --jq .login)
KENNUNG=$(gh api user --jq .id)
ADRESSE="${KENNUNG}+${BENUTZER}@users.noreply.github.com"
echo "Angemeldet als $BENUTZER."

echo
echo "== 2/5  Commits unter dem GitHub-Namen statt der privaten Adresse"
if ! git remote get-url origin >/dev/null 2>&1; then
  export GIT_AUTHOR_NAME="$BENUTZER" GIT_AUTHOR_EMAIL="$ADRESSE"
  export GIT_COMMITTER_NAME="$BENUTZER" GIT_COMMITTER_EMAIL="$ADRESSE"
  git rebase --quiet --root --exec "git commit --amend --reset-author --no-edit --quiet"
  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
fi
git config user.name "$BENUTZER"
git config user.email "$ADRESSE"

echo
echo "== 3/5  Repository $BENUTZER/$REPO_NAME anlegen"
if gh repo view "$BENUTZER/$REPO_NAME" >/dev/null 2>&1; then
  echo "Das Repository gibt es schon."
else
  gh repo create "$REPO_NAME" --public \
    --description "Das Blaue Banner – Wochenblatt der Allianz auf WoW Forever"
fi
git remote get-url origin >/dev/null 2>&1 || \
  git remote add origin "https://github.com/$BENUTZER/$REPO_NAME.git"

echo
echo "== 4/5  Overleaf-Token hinterlegen"
echo "Erzeuge in Overleaf ein neues Git-Token (Account Settings → Git Integration)"
echo "und füge es gleich ein. Die Eingabe bleibt unsichtbar."
gh secret set OVERLEAF_TOKEN --repo "$BENUTZER/$REPO_NAME"

echo
echo "== 5/5  Hochladen, GitHub Pages einschalten, ersten Bau starten"
gh api -X POST "repos/$BENUTZER/$REPO_NAME/pages" -f build_type=workflow >/dev/null 2>&1 || true
git push --quiet -u origin main
gh api -X POST "repos/$BENUTZER/$REPO_NAME/pages" -f build_type=workflow >/dev/null 2>&1 || \
  gh api -X PUT "repos/$BENUTZER/$REPO_NAME/pages" -f build_type=workflow >/dev/null 2>&1 || true

echo
echo "Fertig. Der erste Bau läuft jetzt und dauert ein paar Minuten:"
echo "  https://github.com/$BENUTZER/$REPO_NAME/actions"
echo "Danach ist die Seite hier zu finden:"
echo "  https://$BENUTZER.github.io/$REPO_NAME/"
