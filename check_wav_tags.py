"""
Script de diagnostic : verifie ce que contient reellement un .wav.

Usage :
    python check_wav_tags.py "chemin/vers/fichier.wav"

Affiche :
    - La liste de tous les chunks RIFF (id, taille, position)
    - Le contenu du chunk LIST/INFO (Titre, Artiste, Album...)
    - Le contenu du chunk id3 (tags ID3v2 + pochette eventuelle)

Si toutes les donnees sont presentes ici mais que Windows Explorer
les affiche vides, c'est une limitation native du shell Windows
(qui ne lit pas les metadonnees WAV dans le dialogue Proprietes).
"""

import struct
import sys
from pathlib import Path


def parse_riff_chunks(data: bytes):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        print("ERREUR : fichier non-WAV (signature RIFF/WAVE manquante)")
        return []
    chunks = []
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk_end = pos + 8 + csize + (csize & 1)
        if chunk_end > len(data):
            chunk_end = len(data)
        chunks.append((cid, pos, csize, data[pos + 8:pos + 8 + csize]))
        pos = chunk_end
    return chunks


def parse_list_info(payload: bytes) -> dict:
    if len(payload) < 4 or payload[:4] != b"INFO":
        return {}
    out = {}
    pos = 4
    while pos + 8 <= len(payload):
        code = payload[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack("<I", payload[pos + 4:pos + 8])[0]
        text_end = pos + 8 + size
        if text_end > len(payload):
            text_end = len(payload)
        text = payload[pos + 8:text_end].rstrip(b"\x00").decode("utf-8", errors="replace")
        out[code] = text
        pos = text_end + (size & 1)  # pad to even
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Fichier introuvable : {path}")
        sys.exit(1)

    data = path.read_bytes()
    print(f"\nFichier        : {path}")
    print(f"Taille         : {len(data):,} octets")
    print(f"RIFF size declared : {struct.unpack('<I', data[4:8])[0]:,}")

    chunks = parse_riff_chunks(data)
    print(f"\n--- {len(chunks)} chunks RIFF detectes ---")
    for cid, pos, csize, _ in chunks:
        cid_str = cid.decode("ascii", errors="replace")
        print(f"  [{cid_str!r:8}] offset={pos:>10}  size={csize:>10}")

    print("\n--- Chunk LIST/INFO ---")
    found_list = False
    for cid, _, _, payload in chunks:
        if cid == b"LIST" and len(payload) >= 4 and payload[:4] == b"INFO":
            found_list = True
            info_tags = parse_list_info(payload)
            if not info_tags:
                print("  (vide)")
            else:
                code_labels = {
                    "INAM": "Titre",
                    "IART": "Artiste",
                    "IPRD": "Album",
                    "ICRD": "Date",
                    "IGNR": "Genre",
                    "ITRK": "Piste N°",
                    "IENG": "Album artist",
                    "IMUS": "Compositeur",
                    "ISFT": "Software",
                    "ICMT": "Commentaire",
                }
                for code, val in info_tags.items():
                    label = code_labels.get(code, code)
                    print(f"  {label:14} : {val}")
    if not found_list:
        print("  (aucun chunk LIST/INFO trouve)")

    print("\n--- Chunk id3 (ID3v2) ---")
    found_id3 = False
    for cid, _, csize, payload in chunks:
        if cid in (b"id3 ", b"ID3 "):
            found_id3 = True
            print(f"  Taille payload : {csize} octets")
            try:
                from mutagen.id3 import ID3
                from io import BytesIO
                id3 = ID3(BytesIO(payload))
                for frame_key in sorted(id3.keys()):
                    frame = id3[frame_key]
                    val = str(frame)
                    if len(val) > 80:
                        val = val[:77] + "..."
                    print(f"  {frame_key:8} : {val}")
            except Exception as exc:
                print(f"  (echec parsing ID3 : {exc})")
    if not found_id3:
        print("  (aucun chunk id3 trouve)")

    print("\nConclusion :")
    has_meta = found_list or found_id3
    if has_meta:
        print("  Les metadonnees SONT presentes dans le fichier.")
        print("  Si l'Explorateur Windows les affiche vides, c'est une")
        print("  limitation native de Windows (le shell ne lit pas les")
        print("  metadonnees WAV dans le dialogue Proprietes/Details).")
        print("  Solutions :")
        print("   - Ouvre le fichier dans foobar2000 / MusicBee / VLC : tu")
        print("     verras toutes les metadonnees correctement.")
        print("   - Pour avoir les metadonnees visibles dans Explorer,")
        print("     installe AudioShell (gratuit) qui ajoute un shell")
        print("     extension dediee aux WAV. Sinon, utilise plutot MP3.")
    else:
        print("  Le fichier ne contient AUCUNE metadonnee. Probleme de")
        print("  conversion : verifier les logs du GUI Zotify.")


if __name__ == "__main__":
    main()
