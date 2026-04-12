"""
backup.py
---------
Gestione dei backup della cartella data/ per prevenzione perdita dati.
Permette sia il backup automatico trasparente che il download/restore completo.
"""

import io
import zipfile
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 30


def _get_files_to_backup() -> list[Path]:
    """Restituisce i file nella root di data/, escludendo sottocartelle (come backups/)."""
    files = []
    if not DATA_DIR.exists():
        return files
    for item in DATA_DIR.iterdir():
        if item.is_file():
            files.append(item)
    return files


def create_auto_backup(reason: str) -> None:
    """
    Genera uno zip automatico dello stato corrente di data/ in data/backups/
    limitando il totale a MAX_BACKUPS.
    
    Args:
        reason: motivo del backup (es: "pre_merge", "pre_reset").
    """
    files = _get_files_to_backup()
    if not files:
        # Nulla da backuppare
        return
        
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{timestamp}_{reason}.zip"
    backup_filepath = BACKUP_DIR / backup_filename
    
    with zipfile.ZipFile(backup_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
            
    # Pulizia vecchi backup (mantieni solo gli ultimi MAX_BACKUPS)
    all_backups = sorted(BACKUP_DIR.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime)
    if len(all_backups) > MAX_BACKUPS:
        to_delete = all_backups[:-MAX_BACKUPS]
        for old_backup in to_delete:
            try:
                old_backup.unlink()
            except Exception:
                pass


def get_backup_zip_bytes() -> bytes:
    """Genera e restituisce in RAM il contenuto zip di data/ (usato per download)."""
    buf = io.BytesIO()
    files = _get_files_to_backup()
    
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
            
    buf.seek(0)
    return buf.read()


def restore_from_zip(zip_bytes: bytes) -> None:
    """
    Sovrascrive la cartella data/ con il contenuto dello zip fornito.
    Prima di farlo, effettua un backup automatico "pre_restore".
    """
    create_auto_backup("pre_restore")
    
    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, 'r') as zf:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for member in zf.infolist():
            # Ignoriamo i file annidati (solo file root)
            if member.is_dir() or '/' in member.filename or '\\' in member.filename:
                continue
                
            target_path = DATA_DIR / member.filename
            with zf.open(member) as source, open(target_path, 'wb') as target:
                target.write(source.read())
