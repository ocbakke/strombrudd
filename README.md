# Strømbruddvarsling for Østfold

Et selvstendig, KI-fritt varslingssystem som sjekker Elvia og Norgesnett og
sender e-post når et strømbrudd har journalistisk interesse. Løsningen kjører i
GitHub Actions og trenger ingen server eller påslått PC.

## Dette varsles

- nytt, uplanlagt brudd med minst 50 berørte kunder
- flere mindre brudd med minst 100 berørte kunder samlet i samme kommune
- dobling eller økning på minst 100 berørte kunder
- forventet rettetid som utsettes minst én time
- ny beskrivelse fra nettselskapet
- strømmen tilbake etter et brudd som berørte minst 50 kunder

Planlagt vedlikehold og små enkeltbrudd filtreres bort. Tersklene kan endres
med miljøvariablene som er omtalt nederst.

## Kilder

- Elvias offentlige ArcGIS-data og [strømbruddskart](https://www.elvia.no/strombruddskart/)
- Norgesnetts offentlige ArcGIS-data og [strømstanskart](https://norgesnett.no/stromstans/)

Norgesnett dekker Fredrikstad (unntatt Onsøy) og Hvaler. Elvia dekker resten av
Østfold, inkludert Onsøy. Programmet bruker kommunegrenser i Norgesnetts
offentlige kart for å skille de aktuelle hendelsene fra selskapets øvrige
nettområder.

## Oppsett på GitHub

### 1. Opprett repository

Opprett et nytt repository og legg inn alle filene i denne pakken. Et offentlig
repository er praktisk for hyppige kjøringer fordi bruk av standard
GitHub-hostede runners i offentlige repositories ikke belastes på samme måte
som private repositories. Ingen e-postpassord ligger i filene eller blir
offentlige.

Hvis repositoryet skal være privat, bør du kontrollere inkluderte
GitHub Actions-minutter. Kjøring hvert femte minutt kan bruke mange minutter.

### 2. Legg inn e-postinnstillinger

Gå til **Settings → Secrets and variables → Actions → New repository secret**.
Opprett disse hemmelighetene:

| Navn | Eksempel | Forklaring |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP-server |
| `SMTP_PORT` | `587` | Vanligvis 587 for STARTTLS eller 465 for SSL |
| `SMTP_SECURITY` | `starttls` | `starttls` eller `ssl` |
| `SMTP_USERNAME` | `varsler@example.com` | Brukernavn |
| `SMTP_PASSWORD` | app-passord | Passord eller app-passord |
| `EMAIL_FROM` | `varsler@example.com` | Avsenderadresse |
| `EMAIL_TO` | `redaksjonen@example.com` | Mottaker; flere skilles med komma |

Bruk en egen varslingskonto eller et app-passord. Ikke bruk ditt vanlige
jobbpassord. Noen Microsoft 365-oppsett har slått av SMTP-pålogging; da må
arbeidsgiver åpne dette for kontoen eller dere må bruke en annen SMTP-tjeneste.

### 3. Gi arbeidsflyten skrivetilgang

Gå til **Settings → Actions → General → Workflow permissions** og velg
**Read and write permissions**. Programmet trenger dette for å lagre
`state/outages.json`, slik at det husker hvilke hendelser som allerede er
varslet.

Bruk helst et repository uten branch protection på standardgrenen. Hvis
standardgrenen er beskyttet mot automatiske commits, kan tilstanden ikke
lagres.

### 4. Test e-posten

1. Åpne fanen **Actions**.
2. Velg **Overvåk strømbrudd i Østfold**.
3. Velg **Run workflow**.
4. La **Send bare en testmelding** være aktivert og start kjøringen.

Du skal motta en e-post med emnet «Test av e-postvarsling».

### 5. Kontroller første ordinære kjøring

Kjør arbeidsflyten manuelt igjen med testvalget avslått. Hvis det ikke finnes
et kvalifiserende strømbrudd, står det i loggen at ingen varsler ble sendt.
Tilstandsfilen blir deretter oppdatert automatisk.

## Intervall og begrensninger

Arbeidsflyten er satt til hvert femte minutt, som er GitHubs korteste
planlagte intervall. GitHub garanterer ikke nøyaktig starttid; kjøringer kan bli
forsinket ved stor belastning. Endre `cron` i
`.github/workflows/monitor.yml` hvis 10, 15 eller 30 minutter er tilstrekkelig.

Eksempel for hvert 15. minutt:

```yaml
- cron: "3/15 * * * *"
```

Kartene viser bare hendelser nettselskapene selv har registrert. Enkelte feil i
lavspentnettet kan mangle. E-posten er derfor et tipsverktøy, ikke en komplett
beredskapskanal. Verifiser alltid opplysningene før publisering.

## Juster tersklene

Disse valgfrie miljøvariablene kan legges inn i arbeidsflyten:

| Variabel | Standard | Betydning |
|---|---:|---|
| `MIN_CUSTOMERS` | 50 | Minste enkeltbrudd |
| `MIN_COMBINED_CUSTOMERS` | 100 | Samlet terskel i én kommune |
| `MIN_COUNT_INCREASE` | 100 | Vesentlig kundeøkning |
| `MIN_DELAY_MINUTES` | 60 | Vesentlig utsatt rettetid |
| `CRITICAL_KEYWORDS` | flere ord | Kommaseparerte ord i beskrivelsen |

## Lokal kontroll

Krever Python 3.11 eller nyere og ingen eksterne pakker.

```bash
python3 -m unittest discover -s tests -v
python3 outage_monitor.py --dry-run --state /tmp/ostfold-test-state.json
```

`--dry-run` sender ikke e-post og endrer ikke den virkelige tilstandsfilen.
