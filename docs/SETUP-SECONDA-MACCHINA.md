# Seconda macchina — worker di scraping (operazione una tantum)

Questo setup a due macchine serve **solo ad accelerare la raccolta iniziale** a 10 anni. A lavoro finito i dati rientrano sulla macchina Linux, che torna a gestire da sola tutte e 25 le marche, e il worker viene spento.

| | Primaria (Linux) | Questa (Windows) |
|---|---|---|
| IP pubblico | linea di casa | diverso, via VPN |
| Database | quello di produzione | **proprio**, seminato da un dump |
| Marche | le 13 nuove | le 10 storiche |
| Durata | ~13h | ~21h |

Le due liste sono **disgiunte**: è questa la condizione che rende il rientro dei dati semplice e privo di ambiguità.

> **Perché un database separato.** L'ipotesi iniziale era farlo scrivere direttamente nel Postgres della primaria via LAN. Non è praticabile: questa macchina è un portatile che cambia rete, e appena esce di casa perderebbe il database a metà lavoro. Ha quindi il suo, e i dati si uniscono alla fine.

> **Perché serve la VPN.** Senza, le due macchine uscirebbero dallo stesso IP pubblico e AutoScout vedrebbe la somma delle richieste: nessuna capacità in più, solo il doppio del rischio di blocco. La VPN dà a questa macchina un budget di richieste indipendente — è l'unica ragione per cui il secondo PC porta un vantaggio reale.

---

## 1. Prerequisiti

- **Docker Desktop** installato e avviato (`docker --version` deve rispondere)
- **Git**
- La VPN (Surfshark) attiva
- Il file `autosmart24-seed.dump`, che ti passa la macchina primaria

## 2. Verificare la VPN

**Sulla primaria** annota il riferimento:

```bash
curl -s https://api.ipify.org
```

**Qui**, con la VPN attiva:

```powershell
curl.exe -s https://api.ipify.org
```

Deve essere **diverso** dal riferimento. Se coincide, la VPN non sta instradando il traffico: fermati e sistemala prima di procedere, altrimenti questa macchina non aggiunge nulla e raddoppia solo il rischio di farsi bloccare l'IP di casa.

*(Non serve alcun test verso la rete locale: il database è qui.)*

## 3. Codice e avvio

```powershell
git clone https://github.com/vyperrrr99/AutoSmart24.git
cd AutoSmart24
docker compose -f docker-compose.worker.yml up -d --build
```

Le porte sono sfalsate (Postgres 5435, API 8002, dashboard 5174) per non collidere con nulla.

## 4. Seminare il database

Senza questo passaggio il worker ripartirebbe da zero e ri-arricchirebbe 155.000 annunci già fatti.

```powershell
docker compose -f docker-compose.worker.yml cp autosmart24-seed.dump postgres:/tmp/seed.dump
docker compose -f docker-compose.worker.yml exec -T postgres pg_restore -U autosmart24 -d autosmart24 --no-owner --no-privileges --clean --if-exists /tmp/seed.dump
```

Qualche avviso su oggetti inesistenti durante il `--clean` è normale.

**Poi chiudi le run rimaste aperte nel dump.** Lo snapshot è stato preso mentre la primaria stava scrapando, quindi contiene una run con `status='running'` che qui non proseguirà mai: senza questo passaggio il pannello coda mostrerebbe per sempre "In esecuzione" su una marca ferma, e la dashboard resterebbe a interrogare il server ogni 3 secondi.

```powershell
docker compose -f docker-compose.worker.yml exec -T postgres psql -U autosmart24 -d autosmart24 -c "UPDATE scrape_runs SET status='error', finished_at=now(), phase=NULL WHERE status='running';"
```

Verifica l'esito:

```powershell
curl.exe -s http://localhost:8002/brands | Select-String -Pattern '"slug"' | Measure-Object -Line
```

Devono risultare 25 marche. Controlla anche che i dati ci siano davvero:

```powershell
docker compose -f docker-compose.worker.yml exec -T postgres psql -U autosmart24 -d autosmart24 -c "SELECT brand, count(*) FROM listings GROUP BY brand ORDER BY brand;"
```

## 5. Lanciare le 10 marche storiche

Tutte le marche sono in pausa, quindi lo scheduler non parte da solo: si procede con avvii manuali, uno alla volta. Salva come `run-storiche.ps1`:

```powershell
$api = "http://localhost:8002"
$brands = @("fiat","volkswagen","audi","bmw","mercedes-benz","peugeot","ford","jeep","citroen","renault")

foreach ($b in $brands) {
    Write-Host "AVVIO $b  $(Get-Date -Format HH:mm:ss)"
    Invoke-RestMethod -Method Post -Uri "$api/brands/$b/run-now" | Out-Null
    Start-Sleep -Seconds 10

    do {
        Start-Sleep -Seconds 300
        $run = (Invoke-RestMethod -Uri "$api/brands/$b/runs")[0]
        $q = Invoke-RestMethod -Uri "$api/queue"
        if ($q.current) { Write-Host "   $b - $($q.current.phase) $($q.current.done)  $(Get-Date -Format HH:mm:ss)" }
    } while ($run.status -eq "running")

    Write-Host "FINE $b : $($run.status) seen=$($run.listings_seen) new=$($run.new_listings)"
}
Write-Host "10 marche storiche completate"
```

Avvialo in una finestra che resti aperta:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-storiche.ps1 *>&1 | Tee-Object run-storiche.log
```

La dashboard di questa macchina è su **http://localhost:5174** e mostra il progresso live.

## 6. Cosa NON fare

- **Non toccare le 13 marche della primaria** (nissan, alfa-romeo, hyundai, land-rover, kia, skoda, porsche, cupra, dacia, mini, mg, volvo, lancia). Se le scansionassi anche qui, il merge finale scarterebbe quel lavoro: per quelle marche la primaria è la fonte di verità.
- **Non riattivare le marche né il cron.** Lo scheduler avvierebbe tutte e 25 le marche anche qui.
- **Non usare `docker compose` senza `-f docker-compose.worker.yml`.**

## 7. Note operative Windows

- **Docker Desktop deve restare in esecuzione** per tutte le ~21 ore, e va disattivata la sospensione automatica: se il PC si sospende, lo scraping si ferma (riprende poi da dove era, nulla va perso).
- Il carico è irrisorio — ~0,2% di CPU e 210 MB di RAM: lo scraper passa quasi tutto il tempo ad attendere le pause tra le richieste. Puoi usare il PC normalmente.
- Se cambi rete, non succede nulla di grave: le richieste in corso falliscono e vengono ritentate. Assicurati solo che la VPN si riattivi.

## 8. Se qualcosa va storto

- **Blocco (`status=blocked`)**: la coda si ferma da sola e smette di contattare il sito. Aspetta almeno un'ora, valuta di cambiare server VPN, poi riprendi con `Invoke-RestMethod -Method Post -Uri "http://localhost:8002/queue/resume"`.
- **Avanzamento in qualsiasi momento**: `curl.exe -s http://localhost:8002/queue`

---

## 9. Rientro dei dati sulla macchina Linux

### 9.1 Qui, a lavoro concluso

```powershell
docker compose -f docker-compose.worker.yml exec -T postgres pg_dump -U autosmart24 -Fc autosmart24 > worker.dump
```

Trasferisci `worker.dump` sulla macchina Linux (chiavetta, `scp`, cartella condivisa — è un file solo, ~40 MB).

### 9.2 Sulla macchina Linux

```bash
cd ~/AutoSmart24
bash scripts/merge-from-worker.sh /percorso/worker.dump
```

Lo script, in ordine: rifiuta di partire se c'è uno scraping in corso, fa un backup di sicurezza del database locale, carica il dump in uno schema di appoggio, esegue il merge in **una sola transazione** e verifica l'integrità referenziale.

### 9.3 Come funziona il merge, e perché è sicuro

Si regge su una condizione: **le due macchine lavorano su marche disgiunte e la primaria non tocca mai le dieci marche storiche**. Per quelle dieci, quindi, il worker è l'unica fonte di verità e le sue righe sostituiscono in blocco quelle locali. Non esiste il caso in cui entrambe le parti abbiano modificato la stessa riga e qualcuno debba decidere chi vince — che è la parte in cui i merge sbagliano in silenzio.

- `listings`, `price_history`, `scrape_runs`, `scrape_events` delle dieci marche: **sostituiti**
- Tutto il resto (le quindici marche della primaria): **non toccato**
- `dealers`: fusi per ID AutoScout, tenendo la copia sincronizzata più di recente — un concessionario può vendere auto di marche assegnate a entrambe le macchine, quindi nessuna delle due è autorevole sulla riga
- `price_history`, `scrape_runs` e `scrape_events` hanno ID seriali che collidono fra le due macchine: vengono reinseriti con ID nuovi, e i riferimenti degli eventi alle run rimappati di conseguenza

**Una guardia rifiuta il merge** se il dump non contiene annunci per tutte e dieci le marche: senza, un dump troncato le cancellerebbe dalla primaria invece di aggiornarle.

Verificato da `scripts/test-merge.sh`, che costruisce due database sintetici divergenti, esegue il merge e controlla che il lavoro di entrambe le parti sopravviva (15 controlli). Eseguibile quando vuoi, non tocca i dati reali:

```bash
bash scripts/test-merge.sh
```

### 9.4 Dopo il merge

1. **Spegni il worker** su questa macchina: `docker compose -f docker-compose.worker.yml down`. Due scrittori senza coordinamento sullo stesso dataset non sono una configurazione sostenibile.
2. La macchina Linux torna a gestire tutte e 25 le marche.
3. Per riprendere lo scraping notturno, riattiva le marche dalla dashboard: al momento sono tutte in pausa, quindi finché non lo fai non parte nulla.
