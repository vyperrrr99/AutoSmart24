# Seconda macchina — worker di scraping

Questa macchina arricchisce a **10 anni** le 10 marche storiche, mentre la macchina primaria completa le 13 marche nuove. Le due liste sono disgiunte, quindi le istanze non hanno nulla da coordinare.

Non c'è alcun database locale e nessun merge da fare: questo worker scrive direttamente nel Postgres della macchina primaria.

| | Primaria | Questa |
|---|---|---|
| IP pubblico | quello della linea di casa | diverso (VPN) |
| Database | locale | quello della primaria, via LAN |
| Marche | le 13 nuove | le 10 storiche |

> **Se la seconda macchina è Windows**, salta ai comandi PowerShell nella sezione **9. Windows** in fondo: i passi sono gli stessi, cambia la sintassi.

## 1. Prerequisiti

```bash
docker --version    # se manca: sudo apt install docker.io docker-compose-v2
git --version
```

## 2. Verificare la VPN — da fare PRIMA di tutto il resto

Serve prima il riferimento. **Sulla macchina primaria** esegui:

```bash
curl -s https://api.ipify.org; echo     # annota questo valore
```

Poi, su questa macchina **con la VPN attiva**, due condizioni devono valere insieme:

```bash
curl -s https://api.ipify.org; echo     # deve essere DIVERSO dal valore annotato
nc -zv 192.168.1.121 5434               # deve riuscire ("succeeded" / "open")
```

- **Il primo fallisce** (stesso IP della primaria): la VPN non è attiva. Senza IP distinto questa macchina non aggiunge capacità — le richieste si sommerebbero a quelle della primaria sullo stesso indirizzo, con lo stesso rischio di blocco.
- **Il secondo fallisce**: la VPN è full-tunnel e sta inghiottendo anche la rete locale. In Surfshark si risolve con **Bypasser** (split tunneling) escludendo `192.168.1.0/24`. Senza questo, il database condiviso non funziona.

## 3. Prendere il codice

```bash
git clone https://github.com/vyperrrr99/AutoSmart24.git
cd AutoSmart24
```

Il codice è su `master`: non serve cambiare branch.

## 4. Avviare il worker

```bash
AS24_DB_HOST=192.168.1.121 sudo docker compose -f docker-compose.worker.yml up -d --build
```

Verifica che sia vivo e che veda il database condiviso:

```bash
curl -s http://localhost:8001/brands | head -c 200     # deve elencare 25 marche
```

Se compaiono le 25 marche, la connessione al Postgres della primaria funziona.

> **Non avviare questo worker mentre la primaria sta applicando una migrazione.** Entrambe le istanze eseguono `alembic upgrade head` all'avvio; sullo stesso database, in contemporanea, si bloccherebbero a vicenda. La migrazione `0007` è già stata applicata dalla primaria, quindi qui sarà un no-op.

## 5. Lanciare le 10 marche storiche

Tutte le marche sono in pausa, quindi lo scheduler non parte da solo: si procede solo con avvii manuali. Questo script le esegue in sequenza, una alla volta.

```bash
cat > run-storiche.sh <<'EOF'
#!/usr/bin/env bash
API=http://localhost:8001
for B in fiat volkswagen audi bmw mercedes-benz peugeot ford jeep citroen renault; do
  echo "▶ $B  $(date +%H:%M:%S)"
  curl -s -m 15 -X POST "$API/brands/$B/run-now" >/dev/null
  sleep 10
  while [ "$(curl -s -m 10 "$API/brands/$B/runs" | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["status"])' 2>/dev/null)" = "running" ]; do
    P=$(curl -s -m 10 "$API/queue" | python3 -c 'import sys,json;c=json.load(sys.stdin)["current"];print(f"{c[\"phase\"]} {c[\"done\"]}" if c else "-")' 2>/dev/null)
    echo "   $B · $P · $(date +%H:%M:%S)"
    sleep 300
  done
  echo "■ $B concluso: $(curl -s "$API/brands/$B/runs" | python3 -c 'import sys,json;r=json.load(sys.stdin)[0];print(f"{r[\"status\"]} seen={r[\"listings_seen\"]} new={r[\"new_listings\"]}")' 2>/dev/null)"
done
echo "✔ 10 marche storiche completate"
EOF
chmod +x run-storiche.sh
nohup ./run-storiche.sh > run-storiche.log 2>&1 &
```

`nohup` fa sopravvivere lo script alla chiusura del terminale. Per seguirlo: `tail -f run-storiche.log`.

## 6. Cosa NON fare su questa macchina

- **Non toccare le 13 marche della primaria** (nissan, alfa-romeo, hyundai, land-rover, kia, skoda, porsche, cupra, dacia, mini, mg, volvo, lancia). `BrandRunGuard` vive in memoria di processo e non si coordina tra macchine: due sweep sulla stessa marca si sovrapporrebbero senza che nessuna delle due se ne accorga.
- **Non riattivare le marche** (`/resume`) e non riabilitare il cron: lo scheduler leggerebbe `tracked_brands` dal database condiviso e alle 03:00 avvierebbe **tutte e 25** le marche anche qui.
- **Non lanciare `docker compose` senza `-f docker-compose.worker.yml`**: il file principale avvierebbe un secondo Postgres locale, e questo worker scriverebbe nel database sbagliato.

## 7. Tempi attesi

~211.500 annunci da riscorrere in ricerca, di cui ~76.500 nuovi da arricchire.

| Fase | Tempo stimato |
|---|---|
| Ricerca | ~5h |
| Dettaglio | ~16h |
| **Totale** | **~21h** |

Il carico è irrisorio (~0,2% CPU, 210 MB di RAM): lo scraper passa quasi tutto il tempo ad attendere le pause cortesi tra le richieste. La macchina resta pienamente usabile; serve solo che rimanga accesa e connessa.

## 8. Se qualcosa va storto

- **Un blocco (`status=blocked`)**: la coda si ferma da sola e non tocca più il sito. Riprendi con `curl -X POST http://localhost:8001/queue/resume` — ma prima aspetta almeno un'ora e valuta di cambiare server VPN.
- **Verificare l'avanzamento in qualsiasi momento**: `curl -s http://localhost:8001/queue`
- **La dashboard della primaria** (http://192.168.1.121:5173) mostra anche il lavoro di questa macchina, perché il database è lo stesso.

---

## 9. Windows (PowerShell)

Stessi passi della guida sopra, adattati. Serve **Docker Desktop** installato e avviato (`docker --version` deve rispondere). Non serve `sudo`.

### 9.1 Verificare la VPN

Sulla macchina primaria, annota il riferimento:

```bash
curl -s https://api.ipify.org
```

Poi qui, **con la VPN attiva**:

```powershell
curl.exe -s https://api.ipify.org                              # deve differire dal riferimento
Test-NetConnection -ComputerName 192.168.1.121 -Port 5434      # cerca TcpTestSucceeded : True
```

Se `TcpTestSucceeded` è `False`, la VPN sta inghiottendo anche la rete locale. Surfshark per Windows ha il **Bypasser**: aggiungi `192.168.1.0/24` alle esclusioni e ripeti il test.

### 9.2 Prendere il codice e avviare

```powershell
git clone https://github.com/vyperrrr99/AutoSmart24.git
cd AutoSmart24
$env:AS24_DB_HOST = "192.168.1.121"
docker compose -f docker-compose.worker.yml up -d --build
```

Verifica che veda il database condiviso (deve elencare 25 marche):

```powershell
curl.exe -s http://localhost:8001/brands | Select-String -Pattern '"slug"' | Measure-Object -Line
```

### 9.3 Lanciare le 10 marche storiche

Equivalente PowerShell dello script bash. Salvalo come `run-storiche.ps1`:

```powershell
$api = "http://localhost:8001"
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

Avvialo in una finestra che resti aperta per ~21 ore:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-storiche.ps1 *>&1 | Tee-Object run-storiche.log
```

### 9.4 Note specifiche Windows

- **Docker Desktop deve restare in esecuzione**: se si chiude, il container si ferma. Disattiva la sospensione automatica del PC per la durata del lavoro.
- **Il firewall di Windows** non c'entra qui: la connessione parte da questa macchina verso la primaria, quindi conta il firewall *della primaria* (verificato: nessuno attivo).
- Valgono tutte le avvertenze della sezione **6. Cosa NON fare**: niente `docker compose` senza `-f docker-compose.worker.yml`, non riattivare le marche o il cron, non toccare le 13 marche della primaria.
