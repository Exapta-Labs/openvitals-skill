#!/usr/bin/env bash
# Process HealthSync data into Obsidian health note
# Aggregates ALL unprocessed syncs, uses latest value per metric
set -euo pipefail

DATA_DIR="${HEALTHSYNC_DATA_DIR:-$HOME/.openclaw/workspace/healthsync-server/data}"
VAULT="${HEALTHSYNC_VAULT_PATH:-$HOME/Documents/Obsidian Vaults/OpenClaw-Brain/OpenClaw-Brain}"
PROCESSED_DIR="$DATA_DIR/processed"
YEAR=$(date +%Y)
DATE=$(date +%Y-%m-%d)
NOTE_DIR="$VAULT/Daily Notes/$YEAR"
NOTE_FILE="$NOTE_DIR/health-${DATE}.md"

mkdir -p "$PROCESSED_DIR" "$NOTE_DIR"

# Find ALL unprocessed sync files
FILES=$(find "$DATA_DIR" -maxdepth 1 -name "sync-*.json" -type f 2>/dev/null | sort)
if [[ -z "$FILES" ]]; then
    echo "No unprocessed syncs"
    exit 0
fi
file_count=$(echo "$FILES" | wc -l | tr -d ' ')
if [[ $file_count -eq 0 ]]; then
    echo "No unprocessed syncs"
    exit 0
fi
echo "Found $file_count sync file(s) to process"

# Get latest file
LATEST=$(echo "$FILES" | tail -n 1)
echo "Using latest: $(basename "$LATEST")"

# Aggregate steps (sum if multiple syncs in same day)
TOTAL_STEPS=$(python3 -c "
import json, os
total = 0
files = os.popen('echo \"$FILES\"').read().splitlines()
for f in files:
    try:
        d = json.load(open(f.strip()))
        total = max(total, d.get('steps', 0))  # max, not sum (same day = cumulative)
    except: pass
print(total)
" 2>/dev/null || echo "0")

# Extract values from latest
val() {
    python3 -c "import json; d=json.load(open('$LATEST')); print(${1})" 2>/dev/null || echo "--"
}

# Build note content
CONTENT="# Health Data — ${DATE}

## Actividade
- Passos: ${TOTAL_STEPS}
- Distância: $(val "f\"{d.get('distance_km',0):.1f}\"") km
- Calorias activas: $(val "int(d.get('calories',0))") kcal
- Calorias basais: $(val "int(d.get('basal_energy_burned',0))") kcal
- Exercício: $(val "int(d.get('exercise_minutes',0))") min
- Pisos subidos: $(val "int(d.get('flights_climbed',0))")
- Horas em pé: $(val "int(d.get('stand_hours',0))")

## Sono
- Total: $(val "f\"{d.get('sleep_hours',0):.1f}\"") h
- Core: $(val "f\"{d.get('sleep_core',0):.1f}\"") h
- Deep: $(val "f\"{d.get('sleep_deep',0):.1f}\"") h
- REM: $(val "f\"{d.get('sleep_rem',0):.1f}\"") h
- Na cama: $(val "f\"{d.get('time_in_bed',0):.1f}\"") h

## Coração
- BPM médio: $(val "int(d.get('heart_rate_avg',0))")
- BPM repouso: $(val "int(d.get('resting_heart_rate',0))")
- BPM max: $(val "int(d.get('heart_rate_max',0))")
- BPM min: $(val "int(d.get('heart_rate_min',0))")
- HRV: $(val "int(d.get('heart_rate_variability',0))") ms
- Walking HR: $(val "int(d.get('walking_heart_rate_avg',0))") bpm

## Corpo
- Peso: $(val "f\"{d.get('body_mass',0):.1f}\"") kg
- Altura: $(val "int(d.get('height',0))") cm
- IMC: $(val "f\"{d.get('body_mass_index',0):.1f}\"")
- Gordura: $(val "f\"{d.get('body_fat_pct',0):.1f}\"")%
- Massa magra: $(val "f\"{d.get('lean_body_mass',0):.1f}\"") kg

## Respiratório
- SpO2: $(val "int(d.get('oxygen_saturation',0))")%
- Respiração: $(val "f\"{d.get('respiratory_rate',0):.1f}\"") rpm
- Ruído ambiente: $(val "f\"{d.get('environmental_audio_exposure',0):.0f}\"") dBA"

# Write note (update section if exists, create if not)
if [ -f "$NOTE_FILE" ]; then
    # File exists — check if it has our header
    if grep -q "# Health Data —" "$NOTE_FILE"; then
        # Replace entire file (same day update with latest data)
        echo "$CONTENT" > "$NOTE_FILE"
        echo "Updated: $NOTE_FILE"
    else
        # Append our section
        echo "" >> "$NOTE_FILE"
        echo "$CONTENT" >> "$NOTE_FILE"
        echo "Appended to: $NOTE_FILE"
    fi
else
    echo "$CONTENT" > "$NOTE_FILE"
    echo "Created: $NOTE_FILE"
fi

# Move processed files
count=0
for f in $(echo "$FILES"); do
    mv "$f" "$PROCESSED_DIR/"
    ((count++))
done
echo "Moved $count file(s) to processed/"

# Git commit
cd "$VAULT" && git add -A && git commit -m "health: ${DATE}" 2>/dev/null || true
echo "Done"
